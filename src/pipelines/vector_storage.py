"""Portable text-vector storage, independent of the embedding model."""

import hashlib
import math
import struct
import sys
import zipfile
from array import array

COMPACT = {"member": "vectors.f16", "dtype": "float16-le"}


def vector_member(manifest: dict) -> str:
    if "text_vectors" not in manifest:
        return "vectors.f32"
    if manifest["text_vectors"] != COMPACT:
        raise ValueError("Unsupported text vector storage")
    if "vectors.f32" in manifest["files"]:
        raise ValueError("Conflicting text vector members")
    return COMPACT["member"]


def compact_vectors(raw: bytes) -> bytes:
    import numpy as np

    values = np.frombuffer(raw, dtype="<f4")
    if not np.isfinite(values).all() or np.any(np.abs(values) > 1.002):
        raise ValueError("Invalid source embedding values")
    return values.astype("<f2").tobytes()


def read_vectors(archive: zipfile.ZipFile, manifest: dict) -> bytes:
    """Restore stored values without another rounding step when rebuilding."""
    member = vector_member(manifest)
    other = "vectors.f32" if member == "vectors.f16" else "vectors.f16"
    if other in archive.namelist() or other in manifest["files"]:
        raise ValueError("Conflicting text vector members")
    if (
        type(manifest.get("chunks")) is not int
        or manifest["chunks"] < 1
        or type(manifest["model"].get("dimensions")) is not int
        or manifest["model"]["dimensions"] < 1
    ):
        raise ValueError("Invalid text vector dimensions or count")
    raw = archive.read(member)
    width = 2 if member.endswith(".f16") else 4
    if (
        hashlib.sha256(raw).hexdigest() != manifest["files"].get(member)
        or len(raw) != manifest["chunks"] * manifest["model"]["dimensions"] * width
    ):
        raise ValueError("Existing vector checksum or size mismatch")
    if width == 4:
        return raw
    values = array("f", (value for (value,) in struct.iter_unpack("<e", raw)))
    if sys.byteorder != "little":
        values.byteswap()
    return values.tobytes()


def search_vectors(archive: zipfile.ZipFile, manifest: dict) -> array:
    values = array("f")
    values.frombytes(read_vectors(archive, manifest))
    if sys.byteorder != "little":
        values.byteswap()
    dimensions = manifest["model"]["dimensions"]
    for offset in range(0, len(values), dimensions):
        norm = sum(float(value) ** 2 for value in values[offset : offset + dimensions])
        if not math.isfinite(norm) or abs(norm - 1) > 0.002:
            raise ValueError("Text vector is not normalized")
        if "text_vectors" in manifest:
            scale = math.sqrt(norm)
            for index in range(offset, offset + dimensions):
                values[index] = values[index] / scale
    return values
