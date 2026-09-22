import base64
import hashlib
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipelines.image_embedding import Encoder
from pipelines.image_preprocess import Recipe

MODEL = base64.b64decode(
    "CAk6pQEKSgoMcGl4ZWxfdmFsdWVzEg5pbWFnZV9mZWF0dXJlcyIKUmVkdWNlTWVhbioNCgRh"
    "eGVzQAJAA6ABByoPCghrZWVwZGltcxgAoAECEg1jaGFubmVsX21lYW5zWiYKDHBpeGVsX3Zh"
    "bHVlcxIWChQIARIQCgIIAQoCCAMKAggCCgIIAmIgCg5pbWFnZV9mZWF0dXJlcxIOCgwIARII"
    "CgIIAQoCCANCBAoAEBE="
)


@pytest.fixture
def manifest(tmp_path: Path) -> dict:
    (tmp_path / "model.onnx").write_bytes(MODEL)
    return {
        "schema_version": 1,
        "file": "model.onnx",
        "bytes": len(MODEL),
        "sha256": hashlib.sha256(MODEL).hexdigest(),
        "normalization": "l2",
        "input": "pixel_values",
        "output": "image_features",
        "shape": [1, 3, 2, 2],
        "dimensions": 3,
        "preprocess": asdict(Recipe(size=2, resize_shortest_edge=2)),
    }


def test_offline_encoder_decodes_and_normalizes_real_graph(
    tmp_path: Path, manifest: dict
) -> None:
    encoder = Encoder(tmp_path, manifest)
    image = Image.new("RGB", (7, 5), (30, 60, 90))
    output = BytesIO()
    image.save(output, format="PNG")
    actual = encoder.encode(output.getvalue())
    assert actual.dtype == np.float32
    np.testing.assert_allclose(actual, np.array([1, 2, 3]) / np.sqrt(14), atol=1e-7)
    assert float(np.linalg.norm(actual)) == pytest.approx(1)


@pytest.mark.parametrize(
    "updates",
    [
        {"sha256": "0" * 64},
        {"bytes": 1},
        {"bytes": float(len(MODEL))},
        {"file": "../model.onnx"},
        {"schema_version": 2},
        {"schema_version": True},
        {"dimensions": True},
        {"dimensions": 4},
        {"shape": [1, 3, 4, 4]},
        {"shape": [True, 3, 2, 2]},
        {"shape": [1, 3, 2.0, 2]},
        {"input": "different"},
        {"normalization": "none"},
    ],
)
def test_encoder_rejects_unpinned_or_incompatible_graph(
    tmp_path: Path, manifest: dict, updates: dict
) -> None:
    with pytest.raises(ValueError):
        Encoder(tmp_path, {**manifest, **updates})


def test_encoder_rejects_bad_pixels_and_empty_embedding(
    tmp_path: Path, manifest: dict
) -> None:
    encoder = Encoder(tmp_path, manifest)
    for tensor in (
        np.zeros((3, 2, 2), dtype=np.float32),
        np.full((3, 2, 2), np.nan, dtype=np.float32),
        np.ones((3, 2, 2), dtype=np.float64),
        np.ones((3, 1, 2), dtype=np.float32),
    ):
        with pytest.raises(ValueError):
            encoder.encode_tensor(tensor)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        encoder.encode(b"not an image")


@pytest.mark.parametrize(
    "missing", ["size", "resize_shortest_edge", "mean", "std", "version"]
)
def test_model_recipe_cannot_silently_use_default_fields(
    tmp_path: Path, manifest: dict, missing: str
) -> None:
    del manifest["preprocess"][missing]
    with pytest.raises(ValueError, match="complete recipe"):
        Encoder(tmp_path, manifest)
