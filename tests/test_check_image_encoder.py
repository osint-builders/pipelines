import hashlib
import json
import platform
import subprocess
from pathlib import Path

import check_image_encoder as checker
import numpy as np
import pytest

from pipelines.image_preprocess import Recipe


def manifest() -> dict:
    return {
        "schema_version": 1,
        "id": "test",
        "variant": "fp16-storage",
        "checkpoint": {},
        "file": "model.onnx",
        "sha256": "a" * 64,
        "bytes": 10,
        "input": "pixel_values",
        "output": "image_features",
        "shape": [1, 3, 2, 2],
        "dimensions": 512,
        "normalization": "l2",
        "preprocess": {"size": 2},
    }


def setup_references(tmp_path: Path) -> tuple[Path, Path]:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest()))
    references = tmp_path / "references"
    references.mkdir()
    (references / "input").write_bytes(b"synthetic")
    expected = [1.0] + [0.0] * 511
    records = [
        {
            "id": str(index),
            "kind": "image" if index < 20 else "tensor",
            "file": "input",
            "sha256": hashlib.sha256(b"synthetic").hexdigest(),
            "expected": expected,
        }
        for index in range(23)
    ]
    (references / "probes.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "model": manifest(),
                "tolerances": checker.TOLERANCES,
                "procedural_fixture_sha256": hashlib.sha256(
                    checker.FIXTURES.read_bytes()
                ).hexdigest(),
                "probes": records,
            }
        )
    )
    return manifest_path, references


def measured() -> dict:
    return {
        "normalized": [1.0] + [0.0] * 511,
        "spec": {"model_sha256": "a" * 64},
        "build": {"CGO_ENABLED": "0"},
        "goos": {"Windows": "windows", "Darwin": "darwin", "Linux": "linux"}[
            platform.system()
        ],
        "goarch": {
            "amd64": "amd64",
            "x86_64": "amd64",
            "aarch64": "arm64",
            "arm64": "arm64",
        }[platform.machine().lower()],
        "tensor_sha256": hashlib.sha256(b"synthetic").hexdigest(),
    }


def test_verify_runs_all_images_and_tensors_and_records_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path, references = setup_references(tmp_path)
    commands = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        commands.append(command)
        assert kwargs["timeout"] == 300
        return subprocess.CompletedProcess(command, 0, json.dumps(measured()), "")

    monkeypatch.setattr(checker.subprocess, "run", run)
    output = tmp_path / "report.json"
    report = checker.verify(path, references, tmp_path / "probe", output, path)
    assert report["passed"]
    assert len(commands) == 23
    assert sum("--image" in command for command in commands) == 20
    assert sum("--tensor" in command for command in commands) == 3
    assert all(command[1:3] == ["--repeats", "0"] for command in commands)
    assert all(row["cosine"] == 1 for row in report["probes"])
    assert json.loads(output.read_text())["tolerances"] == checker.TOLERANCES
    assert report["network_isolation"] == {"mode": "none", "enforced": False}


@pytest.mark.parametrize(
    "mode",
    [
        "drift",
        "nan",
        "zero",
        "cgo",
        "wrong_model",
        "wrong_tensor",
        "foreign_target",
        "timeout",
    ],
)
def test_verify_failures_remain_visible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    path, references = setup_references(tmp_path)
    result = measured()
    if mode == "drift":
        result["normalized"] = [0.0, 1.0] + [0.0] * 510
    elif mode == "nan":
        result["normalized"][0] = float("nan")
    elif mode == "zero":
        result["normalized"][0] = 0
    elif mode == "cgo":
        result["build"]["CGO_ENABLED"] = "1"
    elif mode == "wrong_model":
        result["spec"]["model_sha256"] = "b" * 64
    elif mode == "wrong_tensor":
        result["tensor_sha256"] = "b" * 64
    elif mode == "foreign_target":
        result["goarch"] = "wasm"

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 300)
        return subprocess.CompletedProcess(command, 0, json.dumps(result), "")

    monkeypatch.setattr(checker.subprocess, "run", run)
    report = checker.verify(
        path, references, tmp_path / "probe", tmp_path / "report.json", path
    )
    assert not report["passed"]
    assert len(report["probes"]) == 23
    assert any(not row["passed"] for row in report["probes"])


def test_stricter_graph_gate_is_applied() -> None:
    expected = np.zeros(512)
    expected[0] = 1
    actual = expected.copy()
    actual[1] = 0.0001
    actual /= np.linalg.norm(actual)
    assert checker._compare(actual, expected, "image", 512)["passed"]
    assert not checker._compare(actual, expected, "tensor", 512)["passed"]


def test_lock_fixture_and_path_integrity(tmp_path: Path) -> None:
    path, references = setup_references(tmp_path)
    lock = tmp_path / "lock.json"
    changed = manifest()
    changed["sha256"] = "b" * 64
    lock.write_text(json.dumps(changed))
    with pytest.raises(ValueError, match="lock"):
        checker.verify(
            path, references, tmp_path / "probe", tmp_path / "report.json", lock
        )
    (references / "input").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        checker.verify(
            path, references, tmp_path / "probe", tmp_path / "report.json", path
        )
    with pytest.raises(ValueError, match="checksum"):
        checker._local_file(
            references,
            "../manifest.json",
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )


def test_isolation_claim_requires_linux_namespace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(checker.sys, "platform", "win32")
    with pytest.raises(ValueError, match="requires Linux"):
        checker._isolation("linux-netns")


def test_reference_uses_model_recipe_and_exports_three_tensors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest()))
    shapes = []

    class FakeEncoder:
        recipe = Recipe(size=2, resize_shortest_edge=2)

        @classmethod
        def from_manifest(cls, path: Path) -> "FakeEncoder":
            return cls()

        def encode_tensor(self, tensor: np.ndarray) -> np.ndarray:
            shapes.append(tensor.shape)
            vector = np.zeros(512, dtype=np.float32)
            vector[0] = 1
            return vector

    monkeypatch.setattr(checker, "Encoder", FakeEncoder)
    output = tmp_path / "references"
    report = checker.reference(path, output, path)
    assert shapes == [(3, 2, 2)] * 20
    assert len(report["probes"]) == 23
    tensors = [row for row in report["probes"] if row["kind"] == "tensor"]
    assert len(tensors) == 3
    assert all((output / row["file"]).stat().st_size == 48 for row in tensors)
    assert all(len(row["expected"]) == 512 for row in report["probes"])
