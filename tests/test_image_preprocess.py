import base64
import json
import struct
import zlib
from dataclasses import replace
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from pipelines.image_preprocess import (
    LEGACY_RECIPE_VERSION,
    MAX_IMAGE_BYTES,
    RECIPE_VERSION,
    Recipe,
    _decode,
    _resize_crop,
    _tiff_orientation,
    preprocess,
)

PROBES = json.loads(
    (Path(__file__).parent / "fixtures/image_preprocess.json").read_text()
)["probes"]


def encoded(image: Image.Image) -> bytes:
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


@pytest.mark.parametrize("probe", PROBES, ids=lambda value: value["name"])
def test_shared_reference_probes(probe: dict) -> None:
    recipe = Recipe(**probe["recipe"])
    actual = preprocess(base64.b64decode(probe["encoded_base64"]), recipe)
    expected = np.frombuffer(
        base64.b64decode(probe["expected_f32le_base64"]), dtype="<f4"
    ).reshape(3, recipe.size, recipe.size)
    assert actual.dtype == np.float32
    assert actual.flags.c_contiguous
    np.testing.assert_allclose(actual, expected, atol=probe["max_abs_error"], rtol=0)
    np.testing.assert_array_equal(
        preprocess(
            base64.b64decode(probe["encoded_base64"]),
            replace(recipe, version=RECIPE_VERSION),
        ),
        actual,
    )


@pytest.mark.parametrize(
    "probe",
    json.loads((Path(__file__).parent / "fixtures/image_jpeg_decode.json").read_text())[
        "probes"
    ],
    ids=lambda value: value["name"],
)
def test_jpeg_decode_reference(probe: dict) -> None:
    body = base64.b64decode(probe["encoded_base64"])
    expected = np.frombuffer(base64.b64decode(probe["pillow_rgb_base64"]), np.uint8)
    actual = np.asarray(_decode(body)).reshape(-1)
    np.testing.assert_array_equal(actual, expected)
    np.testing.assert_array_equal(
        preprocess(body, Recipe(version=LEGACY_RECIPE_VERSION)),
        preprocess(body, Recipe()),
    )


@pytest.mark.parametrize(
    "width,height,size,edge",
    [(317, 245, 224, 256), (513, 899, 256, 256), (1, 101, 4, 5), (6, 4, 4, 4)],
)
def test_resampler_matches_pillow_bilinear(
    width: int, height: int, size: int, edge: int
) -> None:
    values = np.arange(width * height * 3, dtype=np.uint32).reshape(height, width, 3)
    image = Image.fromarray(((values * 71 + values // 17) % 256).astype(np.uint8))
    reference = image.resize(
        (width * edge // min(width, height), height * edge // min(width, height)),
        Image.Resampling.BILINEAR,
    )
    x, y = round((reference.width - size) / 2), round((reference.height - size) / 2)
    reference = reference.crop((x, y, x + size, y + size))
    np.testing.assert_array_equal(_resize_crop(image, Recipe(size, edge)), reference)


def test_alpha_and_grayscale_are_defined_before_resampling() -> None:
    rgba = Image.new("RGBA", (2, 1))
    rgba.putdata([(13, 255, 0, 0), (12, 40, 200, 128)])
    np.testing.assert_array_equal(
        np.asarray(_decode(encoded(rgba))), [[[255, 255, 255], [133, 147, 227]]]
    )
    gray = Image.new("L", (1, 1), 91)
    np.testing.assert_array_equal(np.asarray(_decode(encoded(gray))), [[[91, 91, 91]]])


def test_normalization_order() -> None:
    recipe = Recipe(1, 1, (0.5, 0.25, 0.75), (0.25, 0.5, 1))
    actual = preprocess(encoded(Image.new("RGB", (1, 1), (255, 128, 0))), recipe)
    expected = np.array([255, 128, 0], dtype=np.float32) / np.float32(255)
    expected -= np.array(recipe.mean, dtype=np.float32)
    expected /= np.array(recipe.std, dtype=np.float32)
    np.testing.assert_array_equal(actual[:, 0, 0], expected)


@pytest.mark.parametrize(
    "recipe",
    [
        replace(Recipe(), size=0),
        replace(Recipe(), size=257),
        replace(Recipe(), resize_shortest_edge=2049),
        replace(Recipe(), size=True),
        replace(Recipe(), version="unknown"),
        replace(Recipe(), std=(0, 1, 1)),
        replace(Recipe(), std=(float("inf"), 1, 1)),
        replace(Recipe(), mean=(float("nan"), 0, 0)),
    ],
)
def test_invalid_recipe(recipe: Recipe) -> None:
    with pytest.raises(ValueError, match="recipe"):
        preprocess(b"", recipe)


@pytest.mark.parametrize("version", [[], {}, None, True, 1])
def test_recipe_rejects_malformed_version(version: object) -> None:
    with pytest.raises(ValueError, match="recipe"):
        replace(Recipe(), version=version).validate()  # type: ignore[arg-type]


@pytest.mark.parametrize("field", ["mean", "std"])
@pytest.mark.parametrize("value", [True, False, "0", None])
def test_recipe_rejects_nonnumeric_channels(field: str, value: object) -> None:
    changes: dict = {field: (value, 1, 1)}
    with pytest.raises(ValueError, match="recipe"):
        replace(Recipe(), **changes).validate()


def chunk(kind: bytes, body: bytes) -> bytes:
    return (
        struct.pack(">I", len(body))
        + kind
        + body
        + struct.pack(">I", zlib.crc32(kind + body))
    )


def test_caps_and_bad_input() -> None:
    valid = encoded(Image.new("RGB", (2, 3)))
    header = struct.pack(">IIBBBBB", 10_001, 4_000, 8, 2, 0, 0, 0)
    oversized = valid[:8] + chunk(b"IHDR", header) + valid[33:]
    for body, message in [
        (b"", "byte limit"),
        (b"x" * (MAX_IMAGE_BYTES + 1), "byte limit"),
        (oversized, "pixel limit"),
        (b"<html>not an image", "JPEG or PNG"),
        (valid[:40], "Invalid"),
        (
            valid[:33] + chunk(b"acTL", struct.pack(">II", 2, 0)) + valid[33:],
            "Animated",
        ),
        (encoded(Image.new("I;16", (2, 3))), "8 bits"),
    ]:
        with pytest.raises(ValueError, match=message):
            preprocess(body, Recipe())


@pytest.mark.parametrize("order,prefix", [("<", b"II"), (">", b"MM")])
def test_bounded_exif_parser(order: str, prefix: bytes) -> None:
    body = prefix + struct.pack(order + "HIH", 42, 8, 1)
    body += struct.pack(order + "HHIH", 274, 3, 1, 7) + b"\0" * 6
    assert _tiff_orientation(body) == 7
    assert _tiff_orientation(body[:17]) == 1
    assert _tiff_orientation(prefix + struct.pack(order + "HI", 42, 0xFFFFFFFF)) == 1


def test_extreme_aspect_ratio_only_materializes_crop() -> None:
    body = encoded(Image.new("RGB", (1, 100_001), (13, 22, 91)))
    actual = preprocess(body, Recipe(4, 2048))
    assert actual.shape == (3, 4, 4)
    np.testing.assert_array_equal(
        actual[:, 0, 0], np.array([13, 22, 91], np.float32) / 255
    )
