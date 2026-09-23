import struct
import zipfile
from pathlib import Path

import pytest

from pipelines.distribution import canonical, sha256, write_bundle
from pipelines.vector_storage import (
    COMPACT,
    compact_vectors,
    read_vectors,
    search_vectors,
)


def bundle(tmp_path: Path, raw: bytes, *, compact: bool = True) -> tuple[Path, dict]:
    member = "vectors.f16" if compact else "vectors.f32"
    manifest = {
        "chunks": 1,
        "model": {"dimensions": 2},
        "files": {member: sha256(raw)},
        **({"text_vectors": COMPACT} if compact else {}),
    }
    path = tmp_path / "dataset.zip"
    write_bundle(path, {member: raw, "manifest.json": canonical(manifest)})
    return path, manifest


def test_half_storage_is_idempotent_and_runtime_normalizes(tmp_path: Path) -> None:
    original = struct.pack("<ff", 0.6, -0.8)
    half = compact_vectors(original)
    assert len(half) == len(original) // 2
    path, manifest = bundle(tmp_path, half)
    with zipfile.ZipFile(path) as archive:
        restored = read_vectors(archive, manifest)
        assert compact_vectors(restored) == half
        values = search_vectors(archive, manifest)
    assert sum(float(v) ** 2 for v in values) == pytest.approx(1, abs=1e-7)
    assert values == pytest.approx([0.6, -0.8], abs=0.0002)


def test_legacy_values_are_preserved(tmp_path: Path) -> None:
    raw = struct.pack("<ff", 0.6, -0.8)
    path, manifest = bundle(tmp_path, raw, compact=False)
    with zipfile.ZipFile(path) as archive:
        assert read_vectors(archive, manifest) == raw
        assert list(search_vectors(archive, manifest)) == list(
            struct.unpack("<ff", raw)
        )


@pytest.mark.parametrize(
    "values", [(0, 0), (2, 0), (float("nan"), 0), (float("inf"), 0)]
)
def test_invalid_half_vectors_fail_validation(tmp_path: Path, values: tuple) -> None:
    path, manifest = bundle(tmp_path, struct.pack("<ee", *values))
    with (
        zipfile.ZipFile(path) as archive,
        pytest.raises(ValueError, match="normalized"),
    ):
        search_vectors(archive, manifest)


@pytest.mark.parametrize("field", ["checksum", "size", "dtype", "conflict", "null"])
def test_storage_binding_is_validated(tmp_path: Path, field: str) -> None:
    path, manifest = bundle(tmp_path, struct.pack("<ee", 1, 0))
    if field == "checksum":
        manifest["files"]["vectors.f16"] = "0" * 64
    elif field == "size":
        manifest["chunks"] = 2
    elif field == "dtype":
        manifest["text_vectors"] = {**COMPACT, "dtype": "int16"}
    elif field == "null":
        manifest["text_vectors"] = None
    else:
        manifest["files"]["vectors.f32"] = "0" * 64
    with zipfile.ZipFile(path) as archive, pytest.raises(ValueError):
        read_vectors(archive, manifest)
