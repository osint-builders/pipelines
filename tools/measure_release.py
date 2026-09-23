"""Measure one native release against the frozen resource contract."""

import argparse
import contextlib
import io
import json
import math
import platform
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from accept_cli import accept
from benchmark_cli import artifact, distribution, hardware, measure
from evaluate_cli import search_args
from release import BINARY_TARGETS, compress_binary

ROOT = Path(__file__).resolve().parents[1]
CONTRACT = ROOT / "tests/fixtures/search_acceptance.json"
RESEARCH_CONTRACT = ROOT / "tests/fixtures/research_release.json"
MODES = ("text", "image", "image_text", "observations_text", "filtered_text")
RESOURCE_CHECKS = (
    "native_target",
    "query_protocol",
    "acceptance",
    "sample_count",
    "stable_responses",
    "latency",
    "memory",
    "executable_size",
    "archive_size",
    "preview_size",
)


def native_target() -> str:
    system = {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}.get(
        platform.system(), "unknown"
    )
    architecture = {
        "x86_64": "amd64",
        "amd64": "amd64",
        "aarch64": "arm64",
        "arm64": "arm64",
    }.get(platform.machine().lower(), "unknown")
    return f"{system}-{architecture}"


def resource_checks(report: dict, contract: dict) -> dict[str, bool]:
    targets = contract["resource_targets"]
    modes = report["modes"]
    latency = targets["latency_seconds"]
    return {
        "native_target": report["target"] == report["native_target"],
        "query_protocol": all(
            modes.get(name, {}).get("args") == args
            for name, args in query_commands(
                report["text_query"], "<query image>"
            ).items()
        ),
        "acceptance": report["acceptance"]["dataset_id"] == report["dataset_id"]
        and report["verification"].get("ok") is True
        and report["verification"].get("dataset_id") == report["dataset_id"]
        and all(
            report["verification"].get(key, 0) > 0
            for key in ("probes", "image_probes", "observation_probes")
        ),
        "sample_count": set(modes) == set(MODES)
        and all(
            len(mode["samples"])
            >= 1 + targets["latency_samples"]["repeated_process_per_mode_min"]
            for mode in modes.values()
        ),
        "stable_responses": all(mode["stable"] is True for mode in modes.values()),
        "latency": all(
            all(
                math.isfinite(sample["seconds"]) and sample["seconds"] > 0
                for sample in mode["samples"]
            )
            and mode["samples"][0]["seconds"]
            <= latency[name if name in {"image", "image_text"} else "text"][
                "first_process_max"
            ]
            and distribution([sample["seconds"] for sample in mode["samples"][1:]])[
                "p95"
            ]
            <= latency[name if name in {"image", "image_text"} else "text"][
                "repeated_process_p95_max"
            ]
            for name, mode in modes.items()
        ),
        "memory": all(
            type(sample["peak_rss_bytes"]) is int
            and 0
            < sample["peak_rss_bytes"]
            <= targets["peak_resident_memory_mib_max"] * 1024**2
            for mode in modes.values()
            for sample in mode["samples"]
        ),
        "executable_size": report["binary"]["bytes"]
        <= targets["standalone_executable_mib_max"] * 1024**2,
        "archive_size": report["archive"]["bytes"]
        <= targets["compressed_release_mib_max"] * 1024**2,
        "preview_size": report["preview_bytes"]
        <= targets["embedded_previews_mib_max"] * 1024**2,
    }


def query_commands(query: dict, image: str) -> dict[str, list[str]]:
    return {
        "text": search_args(query),
        "image": ["search", "--image", image],
        "image_text": ["search", "--image", image, query["query"]],
        "observations_text": ["search", "--observations", *search_args(query)[1:]],
        "filtered_text": ["search", "--where", "manufacturer=Thales", "radar"],
    }


def reference_checks(report: dict, baseline: dict, contract: dict) -> dict[str, bool]:
    targets = contract["resource_targets"]
    fields = (
        "os",
        "architecture",
        "cpu",
        "logical_cpus",
        "physical_memory_bytes",
        "GOMAXPROCS",
    )
    comparable = (
        report["target"] == contract["baseline"]["native_measurement_target"]
        and baseline["dataset"].get("dataset_id") == contract["baseline"]["dataset_id"]
        and all(
            report["hardware"].get(key) == baseline["hardware"].get(key)
            for key in fields
        )
        and report["content_sha256"] == baseline["dataset"]["content_sha256"]
        and report["text_query"] == baseline["latency"]["probe_query"]
    )
    return {
        "reference_comparable": comparable,
        "reference_latency": comparable
        and all(
            distribution(
                [sample["seconds"] for sample in report["modes"][mode]["samples"][1:]]
            )["p95"]
            <= baseline["latency"]["repeat_seconds"]["p95"]
            * targets["text_repeated_process_p95_baseline_multiplier_max"]
            for mode in ("text", "observations_text")
        ),
        "reference_memory": comparable
        and all(
            all(
                sample["peak_rss_bytes"] is not None
                for sample in report["modes"][mode]["samples"]
            )
            and max(
                sample["peak_rss_bytes"] for sample in report["modes"][mode]["samples"]
            )
            <= baseline["peak_rss_bytes"]
            * targets["text_peak_resident_memory_baseline_multiplier_max"]
            for mode in ("text", "observations_text")
        ),
    }


def native_binary(binary: Path, target: str) -> tuple[str, str, str]:
    selected = next(
        (row for row in BINARY_TARGETS if "-".join(row[:2]) == target), None
    )
    if selected is None or native_target() != target or binary.name != selected[2]:
        raise ValueError("Release checks require the named native target binary")
    return selected


def functional_acceptance(binary: Path, bundle: Path, target: str) -> dict:
    native_binary(binary, target)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        accept(binary, bundle)
    accepted = json.loads(captured.getvalue())
    verified, _ = measure([str(binary.resolve()), "verify"])
    required_probes = ["probes"]
    if "image" in manifest:
        required_probes.append("image_probes")
    if "observations" in manifest:
        required_probes.append("observation_probes")
    checks = {
        "dataset_binding": accepted.get("dataset_id") == manifest["dataset_id"]
        and verified.get("dataset_id") == manifest["dataset_id"],
        "verification": verified.get("ok") is True
        and all(verified.get(name, 0) > 0 for name in required_probes),
    }
    return {
        "schema_version": 1,
        "kind": "functional",
        "target": target,
        "native_target": native_target(),
        "dataset_id": manifest["dataset_id"],
        "bundle_sha256": artifact(bundle)["sha256"],
        "binary": artifact(binary),
        "acceptance": accepted,
        "verification": verified,
        "checks": [{"name": name, "passed": passed} for name, passed in checks.items()],
        "functional_checks_passed": all(checks.values()),
    }


def benchmark(
    binary: Path,
    bundle: Path,
    target: str,
    *,
    repeats: int = 20,
    baseline: Path | None = None,
    profile: str = "identification",
) -> dict:
    if repeats < 20:
        raise ValueError("Release measurements require at least 20 repeat processes")
    selected = native_binary(binary, target)
    if profile not in {"identification", "research"}:
        raise ValueError("Unknown release validation profile")
    if profile == "research" and baseline is not None:
        raise ValueError(
            "Fixed-corpus reference comparison uses the identification profile"
        )
    contract_path = RESEARCH_CONTRACT if profile == "research" else CONTRACT
    contract = json.loads(contract_path.read_bytes())
    machine = hardware()
    query = json.loads((ROOT / "tests/fixtures/retrieval.json").read_bytes())[0]
    with zipfile.ZipFile(bundle) as archive, tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads(archive.read("manifest.json"))
        records = json.loads(archive.read("image/index.json"))
        record = next(
            row
            for row in records
            if row["vector_index"] is not None and row["preview"] is not None
        )
        image = Path(temporary) / "query.jpg"
        image.write_bytes(archive.read(record["preview"]["member"]))
        commands = query_commands(query, str(image))
        modes = {}
        for name, args in commands.items():
            samples, first, stable = [], None, True
            for _ in range(repeats + 1):
                response, sample = measure([str(binary.resolve()), *args])
                if response["dataset_id"] != manifest["dataset_id"]:
                    raise ValueError(
                        "Binary dataset does not match the measured bundle"
                    )
                if first is None:
                    first = response
                stable = stable and response == first
                samples.append(sample)
            modes[name] = {
                "budget": name if name in {"image", "image_text"} else "text",
                "args": ["<query image>" if arg == str(image) else arg for arg in args],
                "samples": samples,
                "repeated": distribution([sample["seconds"] for sample in samples[1:]]),
                "stable": stable,
            }
    captured = io.StringIO()
    with contextlib.redirect_stdout(captured):
        accept(binary, bundle)
    verified, _ = measure([str(binary.resolve()), "verify"])
    compressed = binary.parent / compress_binary(binary.parent, selected)
    binary_artifact = artifact(binary)
    report = {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "target": target,
        "native_target": native_target(),
        "hardware": machine,
        "dataset_id": manifest["dataset_id"],
        "content_sha256": manifest["content_sha256"],
        "bundle_sha256": artifact(bundle)["sha256"],
        "contract_sha256": artifact(contract_path)["sha256"],
        "binary_sha256": binary_artifact["sha256"],
        "binary": binary_artifact,
        "archive": artifact(compressed),
        "preview_bytes": manifest["image"]["preview_bytes"],
        "gallery_sha256": manifest["image"]["gallery_sha256"],
        "query_image": {
            "media_id": record["id"],
            "sha256": record["preview"]["sha256"],
        },
        "text_query": query,
        "methodology": "One first observed process followed by at least 20 fresh processes per mode. Filesystem caches are uncontrolled; these are not reboot-cold or resident-service measurements. Hardware is recorded before the first query. Image inputs are embedded previews for resource measurement, not held-out quality.",
        "acceptance": json.loads(captured.getvalue()),
        "verification": verified,
        "modes": modes,
    }
    checks = resource_checks(report, contract)
    if profile == "research":
        report["profile"] = contract["profile"]
    if baseline is not None:
        report["baseline"] = json.loads(baseline.read_bytes())
        checks.update(reference_checks(report, report["baseline"], contract))
    report["checks"] = [
        {"name": name, "passed": passed} for name, passed in checks.items()
    ]
    report["resource_gates_passed"] = all(checks.values())
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument(
        "--profile", choices=["identification", "research"], default="identification"
    )
    parser.add_argument(
        "--functional-only",
        action="store_true",
        help="Check candidate functionality without claiming quality or resource gates",
    )
    args = parser.parse_args()
    if args.functional_only:
        report = functional_acceptance(args.binary, args.bundle, args.target)
    else:
        report = benchmark(
            args.binary,
            args.bundle,
            args.target,
            repeats=args.repeats,
            baseline=args.baseline,
            profile=args.profile,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "checks": report["checks"]}))
    passed_key = (
        "functional_checks_passed" if args.functional_only else "resource_gates_passed"
    )
    if not report[passed_key]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
