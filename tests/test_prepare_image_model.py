import hashlib
from pathlib import Path

import numpy as np
import pytest
from prepare_image_model import SOURCES, checkpoint, validate_export


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
