import builtins
import hashlib
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import numpy as np
import pytest
from prepare_image_model import (
    SOURCES,
    checkpoint,
    configure_export_cpu,
    validate_export,
)


@pytest.mark.parametrize("architecture", ["AMD64", "x86_64"])
def test_export_pins_cpu_before_import(
    architecture: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("prepare_image_model.platform.machine", lambda: architecture)
    monkeypatch.setenv("ATEN_CPU_CAPABILITY", "avx512")
    original_import = builtins.__import__

    def import_torch(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "torch":
            assert os.environ["ATEN_CPU_CAPABILITY"] == "avx2"
            return SimpleNamespace(
                cpu=SimpleNamespace(
                    get_capabilities=lambda: {"avx2": True, "fma3": True}
                ),
                backends=SimpleNamespace(
                    cpu=SimpleNamespace(get_cpu_capability=lambda: "AVX2")
                ),
            )
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", import_torch)
    assert configure_export_cpu() == {
        "architecture": "x86_64",
        "aten_cpu_capability": "avx2",
    }


@pytest.mark.parametrize(
    ("capabilities", "dispatch"),
    [
        ({"avx2": False, "fma3": True}, "AVX2"),
        ({"avx2": True, "fma3": False}, "AVX2"),
        ({"avx2": True, "fma3": True}, "AVX512"),
    ],
)
def test_export_rejects_unsupported_or_already_initialized_cpu(
    capabilities: dict[str, bool], dispatch: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("prepare_image_model.platform.machine", lambda: "x86_64")
    monkeypatch.setenv("ATEN_CPU_CAPABILITY", "avx2")
    monkeypatch.setitem(
        sys.modules,
        "torch",
        SimpleNamespace(
            cpu=SimpleNamespace(get_capabilities=lambda: capabilities),
            backends=SimpleNamespace(
                cpu=SimpleNamespace(get_cpu_capability=lambda: dispatch)
            ),
        ),
    )
    with pytest.raises(ValueError, match="requires"):
        configure_export_cpu()


def test_export_rejects_other_architectures(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("prepare_image_model.platform.machine", lambda: "arm64")
    with pytest.raises(ValueError, match="requires an x86_64"):
        configure_export_cpu()


def test_export_validation_rejects_invalid_or_changed_embeddings() -> None:
    expected = np.ones((1, 512), dtype=np.float32)
    assert validate_export(expected, expected) == {
        "cosine": pytest.approx(1),
        "max_abs": 0,
    }
    for actual in (
        np.zeros_like(expected),
        np.full_like(expected, np.nan),
        -expected,
        expected[:, :-1],
    ):
        with pytest.raises(ValueError):
            validate_export(actual, expected)
    with pytest.raises(ValueError):
        validate_export(expected, np.zeros_like(expected))


def test_checkpoint_reuse_is_verified_and_missing_is_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = {
        **SOURCES["mobileclip2-s0"],
        "bytes": 4,
        "sha256": hashlib.sha256(b"test").hexdigest(),
    }
    monkeypatch.setitem(SOURCES, "test", spec)
    with pytest.raises(ValueError, match="Missing checkpoint"):
        checkpoint(tmp_path, "test", download=False)
    path = tmp_path / "test.safetensors"
    path.write_bytes(b"test")
    assert checkpoint(tmp_path, "test", download=False) == path
    path.write_bytes(b"fake")
    with pytest.raises(ValueError, match="checksum"):
        checkpoint(tmp_path, "test", download=False)
