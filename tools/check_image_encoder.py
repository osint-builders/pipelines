"""Prepare Python ORT references and verify native Go image encoder parity."""

import argparse
import base64
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
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
    if _contract(manifest) != _contract(_json(lock)):
        raise ValueError("Exported model does not match the tracked image model lock")
    if manifest["dimensions"] != 512:
        raise ValueError("The native parity suite requires 512-dimensional embeddings")
    return manifest


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


def _isolation(mode: str) -> dict:
    if mode == "none":
        return {"mode": "none", "enforced": False}
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
    isolation = _isolation(network_isolation)
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
    result = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "model": _contract(manifest),
        "tolerances": TOLERANCES,
        "network_isolation": isolation,
        "host": {"system": platform.system(), "machine": platform.machine()},
        "passed": all(row["passed"] for row in reports),
        "probes": reports,
    }
    _write(output, result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("reference")
    check = commands.add_parser("verify")
    for command in (prepare, check):
        command.add_argument("--manifest", type=Path, required=True)
        command.add_argument("--lock", type=Path, default=DEFAULT_LOCK)
        command.add_argument("--output", type=Path, required=True)
    check.add_argument("--reference", type=Path, required=True)
    check.add_argument("--probe", type=Path, required=True)
    check.add_argument(
        "--network-isolation", choices=("none", "linux-netns"), default="none"
    )
    args = parser.parse_args()
    if args.command == "reference":
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
