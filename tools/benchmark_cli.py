"""Measure retrieval, fresh-process latency, memory, and artifacts without dependencies."""

import argparse
import ctypes
import hashlib
import json
import math
import os
import platform
import statistics
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

from evaluate_cli import evaluate, search_args


class ProcessMemory:
    """Read OS high-water counters; unsupported platforms produce a null metric."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.handle = None
        self.method = "unavailable"
        if sys.platform == "win32":
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("faults", wintypes.DWORD),
                    ("peak_working_set", ctypes.c_size_t),
                    ("working_set", ctypes.c_size_t),
                    *[(f"other_{i}", ctypes.c_size_t) for i in range(6)],
                ]

            self.counter = Counters()
            self.counter.cb = ctypes.sizeof(self.counter)
            self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            self.kernel.OpenProcess.argtypes = [
                wintypes.DWORD,
                wintypes.BOOL,
                wintypes.DWORD,
            ]
            self.kernel.OpenProcess.restype = wintypes.HANDLE
            self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
            self.handle = self.kernel.OpenProcess(0x0410, False, pid)
            self.query = ctypes.WinDLL("psapi").GetProcessMemoryInfo
            self.query.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD]
            self.query.restype = wintypes.BOOL
            self.method = "windows_peak_working_set" if self.handle else "unavailable"
        elif sys.platform.startswith("linux"):
            self.method = "linux_vm_hwm"
        elif sys.platform == "darwin":
            self.method = "darwin_wait4_maxrss"

    def read(self) -> int | None:
        if self.handle and self.query(
            self.handle, ctypes.byref(self.counter), ctypes.sizeof(self.counter)
        ):
            return self.counter.peak_working_set
        if self.method == "linux_vm_hwm":
            try:
                for line in Path(f"/proc/{self.pid}/status").read_text().splitlines():
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1]) * 1024
            except (FileNotFoundError, PermissionError, ProcessLookupError):
                pass
        return None

    def close(self) -> None:
        if self.handle:
            self.kernel.CloseHandle(self.handle)


def measure(command: list[str], *, timeout: float = 120) -> tuple[dict, dict]:
    start = time.perf_counter()
    peak = None
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        with subprocess.Popen(command, stdout=output, stderr=errors) as process:
            memory = ProcessMemory(process.pid)
            try:
                while True:
                    value = memory.read()
                    if value is not None:
                        peak = max(peak or 0, value)
                    if time.perf_counter() - start > timeout:
                        raise subprocess.TimeoutExpired(command, timeout)
                    if sys.platform == "darwin":
                        pid, status, usage = os.wait4(process.pid, os.WNOHANG)
                        if pid:
                            process.returncode = os.waitstatus_to_exitcode(status)
                            peak = int(usage.ru_maxrss)
                            break
                        time.sleep(0.02)
                        continue
                    try:
                        process.wait(timeout=0.02)
                        value = memory.read()
                        if value is not None:
                            peak = max(peak or 0, value)
                        break
                    except subprocess.TimeoutExpired:
                        pass
            finally:
                memory.close()
                if process.poll() is None:
                    process.kill()
                    process.wait()
            output.seek(0)
            errors.seek(0)
            raw = output.read()
            if process.returncode:
                raise subprocess.CalledProcessError(
                    process.returncode, command, raw, errors.read()
                )
    response = json.loads(raw)
    return response, {
        "seconds": round(time.perf_counter() - start, 6),
        "peak_rss_bytes": peak,
        "memory_method": memory.method,
    }


def distribution(values: list[float]) -> dict:
    ordered = sorted(values)
    return {
        "count": len(values),
        "median": statistics.median(ordered) if ordered else None,
        "p95": ordered[math.ceil(len(ordered) * 0.95) - 1] if ordered else None,
        "max": max(ordered) if ordered else None,
    }


def quality(rows: list[dict]) -> dict:
    count = len(rows)
    return {
        "count": count,
        "passed": sum(row["passed"] for row in rows),
        "top1": sum(row["rank"] == 1 for row in rows),
        "recall_at_5": sum(0 < (row["rank"] or 21) <= 5 for row in rows) / count
        if count
        else None,
        "mrr_at_20": sum(1 / row["rank"] if row["rank"] else 0 for row in rows) / count
        if count
        else None,
    }


def summarize(rows: list[dict]) -> dict:
    annotated = [
        {
            **row,
            "cohort": "required" if row.get("required", True) else "exploratory",
            "task": row.get("task")
            or (
                "hybrid_name_or_designation"
                if row["mode"] == "hybrid"
                else "vector_description_or_specification"
            ),
        }
        for row in rows
    ]
    return {
        "overall": quality(annotated),
        **{
            f"by_{key}": {
                value: quality([row for row in annotated if row[key] == value])
                for value in sorted({row[key] for row in annotated})
            }
            for key in ("cohort", "source", "task")
        },
    }


def artifact(path: Path) -> dict:
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    return {"path": str(path.resolve()), "bytes": path.stat().st_size, "sha256": digest}


def hardware() -> dict:
    cpu = platform.processor()
    ram = None
    if sys.platform == "win32":
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            cpu = winreg.QueryValueEx(key, "ProcessorNameString")[0].strip()

        class MemoryStatus(ctypes.Structure):
            _fields_ = [
                ("length", ctypes.c_ulong),
                ("load", ctypes.c_ulong),
                *[(f"size_{i}", ctypes.c_ulonglong) for i in range(7)],
            ]

        status = MemoryStatus()
        status.length = ctypes.sizeof(status)
        if ctypes.WinDLL("kernel32").GlobalMemoryStatusEx(ctypes.byref(status)):
            ram = status.size_0
    elif hasattr(os, "sysconf"):
        try:
            ram = os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        except (ValueError, OSError):
            pass
    if sys.platform == "darwin":
        cpu = subprocess.check_output(
            ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
        ).strip()
        ram = int(subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True))
    elif sys.platform.startswith("linux"):
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith(("model name", "Hardware")):
                cpu = line.partition(":")[2].strip()
                break
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": cpu,
        "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": ram,
        "python": platform.python_version(),
        "GOMAXPROCS": os.environ.get("GOMAXPROCS"),
    }


def benchmark(binary: Path, cases: Path, releases: Path, repeats: int) -> dict:
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    probes = []
    case = json.loads(cases.read_text(encoding="utf-8"))[0]
    command = [str(binary.resolve()), *search_args(case)]
    for _ in range(repeats + 1):
        _, sample = measure(command)
        probes.append(sample)
    samples = []
    info = {}

    def run(*args: str) -> dict:
        nonlocal info
        response, sample = measure([str(binary.resolve()), *args])
        if args[0] == "info":
            info = response
        else:
            samples.append({"query": args[-1], **sample})
            if len(samples) % 10 == 0:
                print(
                    f"Measured {len(samples)} retrieval cases",
                    file=sys.stderr,
                    flush=True,
                )
        return response

    evaluation = evaluate(binary, cases, runner=run)
    memory_values = [
        row["peak_rss_bytes"]
        for row in [*probes, *samples]
        if row["peak_rss_bytes"] is not None
    ]
    return {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "hardware": hardware(),
        "binary": artifact(binary),
        "cases": artifact(cases),
        "dataset": info,
        "archives": [
            artifact(path)
            for path in sorted(releases.iterdir())
            if path.name.endswith((".zip", ".tar.xz"))
        ],
        "methodology": {
            "latency": "Wall time from process launch through JSON output parsing; includes model and dataset loading.",
            "first_launch": "First search in this run, before info and artifact hashing. OS filesystem cache is uncontrolled; not a reboot-cold measurement.",
            "repeat_launch": "Fresh processes repeat the same query; OS file cache may be warm. There is no resident model or in-process warm query.",
            "memory": "Per-process OS peak resident memory: Windows/Linux high-water counters sampled every 20 ms; macOS wait4 returns the completed child's maximum RSS in bytes. Missing Linux final counters may omit the last 20 ms; unsupported systems report null.",
            "percentiles": "Nearest-rank p95; probe repetitions describe one query, suite samples cover all evaluated cases once.",
            "quality": "Source-filtered fixtures with one expected entity per query; required and exploratory cases remain separate. Missing task labels use mode-based groups, not reviewed task annotations.",
        },
        "latency": {
            "probe_query": case,
            "first_launch": probes[0],
            "repeat_launches": probes[1:],
            "repeat_seconds": distribution([row["seconds"] for row in probes[1:]]),
            "suite_seconds": distribution([row["seconds"] for row in samples]),
        },
        "peak_rss_bytes": max(memory_values) if memory_values else None,
        "samples": samples,
        "quality": summarize(evaluation["results"]),
        "evaluation": evaluation,
        "ok": evaluation["ok"] and not evaluation["skipped"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--cases", type=Path, default=Path("tests/fixtures/retrieval.json")
    )
    parser.add_argument("--releases", type=Path, default=Path("dist/release"))
    parser.add_argument("--repeats", type=int, default=5)
    parser.add_argument("--output", type=Path, default=Path("build/m1/baseline.json"))
    args = parser.parse_args()
    report = benchmark(args.binary, args.cases, args.releases, args.repeats)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "ok": report["ok"],
                "quality": report["quality"],
            },
            indent=2,
        )
    )
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
