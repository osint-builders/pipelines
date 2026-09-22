"""Fetch locked model bytes, prepare Python references, and verify native parity."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import socket
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from pipelines.image_embedding import Encoder
from pipelines.image_preprocess import preprocess

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LOCK = ROOT / "src/pipelines/image_model.lock.json"
FIXTURES = ROOT / "tests/fixtures/image_preprocess.json"
TOLERANCES: dict = {
    "image": {"min_cosine": 0.999, "max_abs_error": 0.01},
    "tensor": {"min_cosine": 0.999999, "max_abs_error": 0.00002},
    "max_unit_norm_error": 0.00001,
}
CONTRACT_KEYS = (
    "schema_version",
    "id",
    "variant",
    "checkpoint",
    "file",
    "sha256",
    "bytes",
    "input",
    "output",
    "shape",
    "dimensions",
    "normalization",
    "preprocess",
)


def _json(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _contract(manifest: dict) -> dict:
    return {key: manifest[key] for key in CONTRACT_KEYS}


def _manifest(path: Path, lock: Path) -> dict:
    manifest = _json(path)
    expected = _contract(_json(lock))
    changed = [
        key for key, value in _contract(manifest).items() if value != expected[key]
    ]
    if changed:
        raise ValueError(
            "Model does not match the tracked image model lock: " + ", ".join(changed)
        )
    if manifest["dimensions"] != 512:
        raise ValueError("The native parity suite requires 512-dimensional embeddings")
    return manifest


def fetch(directory: Path, lock: Path = DEFAULT_LOCK) -> dict:
    """Download the immutable release asset and publish only checksum-verified bytes."""
    body = lock.read_bytes()
    manifest = json.loads(body)
    name, checksum, size = manifest["file"], manifest["sha256"], manifest["bytes"]
    if (
        not isinstance(name, str)
        or re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9._-]*\.onnx", name) is None
        or not isinstance(checksum, str)
        or re.fullmatch(r"[a-f0-9]{64}", checksum) is None
        or type(size) is not int
        or size <= 0
    ):
        raise ValueError("Invalid locked image model filename, checksum, or size")

    def verified(path: Path) -> None:
        if not path.is_file() or path.stat().st_size != size:
            raise ValueError(
                "Locked image model is missing or has an incorrect byte count"
            )
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != checksum:
                raise ValueError("Locked image model checksum mismatch")

    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / name
    cache_hit = destination.exists()
    if cache_hit:
        verified(destination)
    with tempfile.TemporaryDirectory(
        prefix=".image-model-", dir=directory
    ) as temporary:
        staging = Path(temporary)
        if not cache_hit:
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    "image-model-" + checksum,
                    "--repo",
                    "osint-builders/pipelines",
                    "--pattern",
                    name,
                    "--dir",
                    str(staging),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
            downloaded = staging / name
            verified(downloaded)
            downloaded.replace(destination)
        metadata = staging / Path(name).with_suffix(".json").name
        metadata.write_bytes(body)
        manifest_path = directory / metadata.name
        metadata.replace(manifest_path)
    return {
        "model": str(destination),
        "manifest": str(manifest_path),
        "sha256": checksum,
        "cache_hit": cache_hit,
    }


def _vector(value: object, dimensions: int) -> np.ndarray:
    vector = np.asarray(value, dtype=np.float64)
    if vector.shape != (dimensions,) or not np.isfinite(vector).all():
        raise ValueError("Expected a finite embedding with the pinned dimensions")
    norm = float(np.linalg.norm(vector))
    if abs(norm - 1) > TOLERANCES["max_unit_norm_error"]:
        raise ValueError("Expected a unit-normalized embedding")
    return vector


def reference(manifest_path: Path, output: Path, lock: Path = DEFAULT_LOCK) -> dict:
    manifest = _manifest(manifest_path, lock)
    encoder = Encoder.from_manifest(manifest_path)
    fixture = _json(FIXTURES)
    probes = fixture["probes"]
    if len(probes) != 20 or len({probe["name"] for probe in probes}) != 20:
        raise ValueError("Expected the pinned 20 procedural image probes")
    output.mkdir(parents=True, exist_ok=True)
    records = []
    for index, probe in enumerate(probes):
        name = probe["name"]
        if re.fullmatch(r"[a-z0-9_]+", name) is None:
            raise ValueError("Invalid probe name")
        body = base64.b64decode(probe["encoded_base64"], validate=True)
        suffix = "png" if body.startswith(b"\x89PNG") else "jpg"
        relative = f"images/{name}.{suffix}"
        path = output / relative
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(body)
        tensor = preprocess(body, encoder.recipe)
        expected = _vector(encoder.encode_tensor(tensor), manifest["dimensions"])
        record = {
            "id": name,
            "kind": "image",
            "file": relative,
            "sha256": _sha(body),
            "expected": expected.tolist(),
        }
        records.append(record)
        if index in {0, 12, 16}:
            relative = f"tensors/{name}.f32le"
            path = output / relative
            path.parent.mkdir(exist_ok=True)
            tensor_body = tensor.astype("<f4").tobytes(order="C")
            path.write_bytes(tensor_body)
            records.append(
                {
                    "id": name + "_tensor",
                    "kind": "tensor",
                    "file": relative,
                    "sha256": _sha(tensor_body),
                    "expected": expected.tolist(),
                }
            )
    result = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "model": _contract(manifest),
        "tolerances": TOLERANCES,
        "procedural_fixture_sha256": _sha(FIXTURES.read_bytes()),
        "reference_runtime": {
            name: importlib.metadata.version(name)
            for name in ("onnxruntime", "numpy", "pillow")
        },
        "reference_platform": {
            "system": platform.system(),
            "machine": platform.machine(),
        },
        "probes": records,
    }
    _write(output / "probes.json", result)
    return result


def _local_file(directory: Path, name: str, checksum: str) -> Path:
    path = (directory / name).resolve()
    if (
        not path.is_relative_to(directory.resolve())
        or _sha(path.read_bytes()) != checksum
    ):
        raise ValueError("Reference probe path or checksum mismatch")
    return path


def _canary(probe: Path, address: str) -> dict:
    completed = subprocess.run(
        [str(probe.resolve()), "--network-canary", address],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    result = json.loads(completed.stdout)
    if not isinstance(result, dict) or result.get("address") != address:
        raise ValueError("Invalid network canary response")
    return result


def network_baseline(probe: Path, output: Path) -> dict:
    addresses = sorted(
        {
            item[4][0]
            for item in socket.getaddrinfo(
                "api.github.com",
                443,
                family=socket.AF_INET,
                type=socket.SOCK_STREAM,
            )
        }
    )
    for address in addresses[:3]:
        result = _canary(probe, f"{address}:443")
        if result.get("reachable") is True and result.get("policy_denied") is False:
            baseline = {
                "schema_version": 1,
                "probe_sha256": _sha(probe.read_bytes()),
                "canary": result,
            }
            _write(output, baseline)
            return baseline
    raise ValueError(
        "No reachable GitHub TCP canary; offline enforcement is unverified"
    )


def _denied_canary(probe: Path, baseline: dict) -> dict:
    if (
        baseline.get("schema_version") != 1
        or baseline.get("probe_sha256") != _sha(probe.read_bytes())
        or baseline.get("canary", {}).get("reachable") is not True
        or baseline["canary"].get("policy_denied") is not False
    ):
        raise ValueError("Network baseline does not match this probe executable")
    result = _canary(probe, baseline["canary"]["address"])
    expected_errors = {10013} if sys.platform == "win32" else {1, 13}
    if (
        result.get("reachable") is not False
        or result.get("policy_denied") is not True
        or result.get("errno") not in expected_errors
    ):
        raise ValueError(
            "Canary did not report policy denial; timeout/refusal is insufficient"
        )
    return result


def _windows_firewall(probe: Path, rule: str) -> dict:
    if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", rule) is None:
        raise ValueError("Expected the exact CI firewall rule name")
    script = """
$ErrorActionPreference = 'Stop'
$rules = @(Get-NetFirewallRule -PolicyStore ActiveStore -Name $env:PIPELINES_PARITY_RULE)
if ($rules.Count -ne 1) { throw 'Expected one active parity firewall rule' }
$rule = $rules[0]
$application = $rule | Get-NetFirewallApplicationFilter
$port = $rule | Get-NetFirewallPortFilter
$address = $rule | Get-NetFirewallAddressFilter
$profiles = @(Get-NetFirewallProfile -PolicyStore ActiveStore)
$enabled = @($profiles | Where-Object { $_.Enabled.ToString() -eq 'True' })
@{
  name = $rule.Name; program = $application.Program
  enabled = ($rule.Enabled.ToString() -eq 'True')
  action = $rule.Action.ToString(); direction = $rule.Direction.ToString()
  profile = $rule.Profile.ToString(); protocol = $port.Protocol.ToString()
  local_port = @($port.LocalPort); remote_port = @($port.RemotePort)
  local_address = @($address.LocalAddress); remote_address = @($address.RemoteAddress)
  service_running = ((Get-Service MpsSvc).Status.ToString() -eq 'Running')
  profiles_enabled = ($profiles.Count -eq 3 -and $enabled.Count -eq 3)
} | ConvertTo-Json -Compress
"""
    environment = os.environ.copy()
    environment["PIPELINES_PARITY_RULE"] = rule
    completed = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
        env=environment,
    )
    result = json.loads(completed.stdout)
    if (
        result.get("name") != rule
        or str(result.get("program", "")).casefold() != str(probe.resolve()).casefold()
        or result.get("enabled") is not True
        or result.get("service_running") is not True
        or result.get("profiles_enabled") is not True
        or result.get("action") != "Block"
        or result.get("direction") != "Outbound"
        or result.get("profile") != "Any"
        or result.get("protocol") not in {"Any", "256"}
        or any(
            result.get(key) != ["Any"]
            for key in (
                "local_port",
                "remote_port",
                "local_address",
                "remote_address",
            )
        )
    ):
        raise ValueError(
            "Active Windows firewall policy does not block this probe's outbound traffic"
        )
    return result


def _isolation(
    mode: str,
    probe: Path | None = None,
    baseline: dict | None = None,
    firewall_rule: str = "",
) -> dict:
    if mode == "none":
        return {"mode": "none", "enforced": False}
    if mode in {"macos-sandbox", "windows-firewall"}:
        expected = "darwin" if mode == "macos-sandbox" else "win32"
        if sys.platform != expected or probe is None or baseline is None:
            raise ValueError(
                "Network restriction requires its native platform and a probe baseline"
            )
        result = {
            "mode": mode,
            "enforced": True,
            "probe_sha256": baseline.get("probe_sha256"),
            "baseline": baseline.get("canary"),
        }
        if mode == "windows-firewall":
            result["policy"] = _windows_firewall(probe, firewall_rule)
            result["scope"] = (
                "probe outbound traffic; other executables are not restricted"
            )
        else:
            result["scope"] = (
                "verifier and child processes under sandbox-exec deny network*"
            )
        result["before"] = _denied_canary(probe, baseline)
        return result
    if sys.platform != "linux":
        raise ValueError("Linux network namespace isolation requires Linux")
    current = os.readlink("/proc/self/ns/net")
    initial = os.readlink("/proc/1/ns/net")
    interfaces = [
        line.split(":", 1)[0].strip()
        for line in Path("/proc/net/dev").read_text().splitlines()[2:]
    ]
    if current == initial or any(name != "lo" for name in interfaces):
        raise ValueError("Network namespace is not isolated from host interfaces")
    return {"mode": mode, "enforced": True, "interfaces": interfaces}


def _compare(actual: object, expected: object, kind: str, dimensions: int) -> dict:
    left, right = _vector(actual, dimensions), _vector(expected, dimensions)
    cosine = float(np.dot(left, right) / (np.linalg.norm(left) * np.linalg.norm(right)))
    cosine = min(1.0, max(-1.0, cosine))
    maximum = float(np.max(np.abs(left - right)))
    tolerance = TOLERANCES[kind]
    return {
        "cosine": cosine,
        "max_abs_error": maximum,
        "unit_norm_error": abs(float(np.linalg.norm(left)) - 1),
        "passed": cosine >= tolerance["min_cosine"]
        and maximum <= tolerance["max_abs_error"],
    }


def verify(
    manifest_path: Path,
    references: Path,
    probe: Path,
    output: Path,
    lock: Path = DEFAULT_LOCK,
    network_isolation: str = "none",
    network_baseline_path: Path | None = None,
    firewall_rule: str = "",
) -> dict:
    manifest = _manifest(manifest_path, lock)
    reference_data = _json(references / "probes.json")
    if (
        reference_data.get("schema_version") != 1
        or reference_data.get("model") != _contract(manifest)
        or reference_data.get("tolerances") != TOLERANCES
        or reference_data.get("procedural_fixture_sha256")
        != _sha(FIXTURES.read_bytes())
    ):
        raise ValueError(
            "Reference model, fixture or tolerance contract does not match"
        )
    records = reference_data["probes"]
    if (
        sum(row["kind"] == "image" for row in records) != 20
        or sum(row["kind"] == "tensor" for row in records) != 3
        or len(records) != 23
        or len({row["id"] for row in records}) != 23
    ):
        raise ValueError("Expected 20 image probes and three tensor probes")
    baseline = _json(network_baseline_path) if network_baseline_path else None
    try:
        isolation = _isolation(network_isolation, probe, baseline, firewall_rule)
    except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
        failure = {
            "schema_version": 1,
            "model": _contract(manifest),
            "passed": False,
            "network_isolation": {
                "mode": network_isolation,
                "enforced": False,
                "error": str(error),
            },
            "probes": [],
        }
        _write(output, failure)
        return failure
    reports = []
    for row in records:
        path = _local_file(references, row["file"], row["sha256"])
        command = [str(probe.resolve()), "--repeats", "0"]
        if row["kind"] == "image":
            command += [
                "--manifest",
                str(manifest_path.resolve()),
                "--image",
                str(path),
            ]
        else:
            command += [
                "--model",
                str((manifest_path.parent / manifest["file"]).resolve()),
                "--sha256",
                manifest["sha256"],
                "--tensor",
                str(path),
                "--height",
                str(manifest["shape"][2]),
                "--width",
                str(manifest["shape"][3]),
                "--dimensions",
                str(manifest["dimensions"]),
                "--input",
                manifest["input"],
                "--output",
                manifest["output"],
            ]
        report = {"id": row["id"], "kind": row["kind"], "passed": False}
        try:
            completed = subprocess.run(
                command, check=True, capture_output=True, text=True, timeout=300
            )
            measured = json.loads(completed.stdout)
            report.update(
                _compare(
                    measured["normalized"],
                    row["expected"],
                    row["kind"],
                    manifest["dimensions"],
                )
            )
            if measured["spec"]["model_sha256"] != manifest["sha256"]:
                raise ValueError("Probe reported a different model checksum")
            if measured.get("build", {}).get("CGO_ENABLED") != "0":
                raise ValueError("Probe was not built with CGO_ENABLED=0")
            host_os = {"Linux": "linux", "Windows": "windows", "Darwin": "darwin"}.get(
                platform.system()
            )
            host_arch = {
                "amd64": "amd64",
                "x86_64": "amd64",
                "arm64": "arm64",
                "aarch64": "arm64",
            }.get(platform.machine().lower())
            if (measured.get("goos"), measured.get("goarch")) != (host_os, host_arch):
                raise ValueError("Probe target does not match the native host")
            if (
                row["kind"] == "tensor"
                and measured.get("tensor_sha256") != row["sha256"]
            ):
                raise ValueError("Probe reported a different input tensor checksum")
            report["runtime"] = {
                key: measured.get(key)
                for key in (
                    "go_version",
                    "goos",
                    "goarch",
                    "load_ms",
                    "preprocess_ms",
                    "first_ms",
                    "total_first_ms",
                    "heap_bytes",
                    "go_sys_bytes",
                    "build",
                    "modules",
                )
            }
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            report.update(passed=False, error=str(error))
            if isinstance(error, subprocess.CalledProcessError):
                report["stderr"] = (error.stderr or "")[-5000:]
        reports.append(report)
    if baseline is not None and network_isolation in {
        "macos-sandbox",
        "windows-firewall",
    }:
        try:
            isolation["after"] = _denied_canary(probe, baseline)
        except (ValueError, KeyError, OSError, subprocess.SubprocessError) as error:
            isolation.update(enforced=False, error=str(error))
    result = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "model": _contract(manifest),
        "tolerances": TOLERANCES,
        "network_isolation": isolation,
        "host": {"system": platform.system(), "machine": platform.machine()},
        "passed": all(row["passed"] for row in reports)
        and (network_isolation == "none" or isolation["enforced"]),
        "probes": reports,
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("fetch")
    download.add_argument("--output", type=Path, required=True)
    download.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
    prepare = commands.add_parser("reference")
    check = commands.add_parser("verify")
    baseline = commands.add_parser("network-baseline")
    baseline.add_argument("--probe", type=Path, required=True)
    baseline.add_argument("--output", type=Path, required=True)
    for command in (prepare, check):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
        command.add_argument("--output", type=Path, required=True)
    check.add_argument("--reference", type=Path, required=True)
    check.add_argument("--probe", type=Path, required=True)
    check.add_argument("--network-baseline", type=Path)
    check.add_argument("--firewall-rule", default="")
    check.add_argument(
        "--network-isolation",
        choices=("none", "linux-netns", "macos-sandbox", "windows-firewall"),
        default="none",
    )
    args = parser.parse_args()
    if args.command == "fetch":
        print(json.dumps(fetch(args.output, args.lock)))
    elif args.command == "network-baseline":
        network_baseline(args.probe, args.output)
        print(json.dumps({"baseline": str(args.output), "reachable": True}))
    elif args.command == "reference":
        result = reference(args.manifest, args.output, args.lock)
        print(
            json.dumps({"reference": str(args.output), "probes": len(result["probes"])})
        )
    else:
        result = verify(
            args.manifest,
            args.reference,
            args.probe,
            args.output,
            args.lock,
            args.network_isolation,
            args.network_baseline,
            args.firewall_rule,
        )
        print(
            json.dumps(
                {
                    "report": str(args.output),
                    "passed": result["passed"],
                    "probes": len(result["probes"]),
                }
            )
        )
        if not result["passed"]:
            raise SystemExit(1)


if __name__ == "__main__":
    main()
