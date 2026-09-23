"""Portable MobileCLIP pixels: EXIF, white alpha, Pillow bilinear AA, center crop.

Resampling uses pixel centers, clipped/renormalized triangle support, signed
22-bit coefficients and uint8 rounding after each separable pass. Crop offsets
round ties to even. Normalization executes float32 division, subtraction, then
division. ICC profiles and gamma chunks do not alter decoded sample values.
Python pixels are unchanged between v1 and v2. Version 2 binds the Go decoder's
centered JPEG chroma interpolation; v1 remains available for archived bundles.
"""

import math
import struct
from dataclasses import dataclass
from io import BytesIO

import numpy as np
from numpy.typing import NDArray
from PIL import Image

LEGACY_RECIPE_VERSION = "exif-white-pillow-bilinear-aa-center-f32-v1"
RECIPE_VERSION = "exif-white-pillow-bilinear-aa-center-f32-v2"
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
_PRECISION = 1 << 22
_PNG = b"\x89PNG\r\n\x1a\n"


def gallery_decode_identity() -> dict[str, str]:
    from PIL import __version__

    return {"version": "static-webp-to-png-v1", "pillow": __version__}


def gallery_bytes(body: bytes) -> bytes:
    """Losslessly decode static archived WebP; query inputs stay JPEG/PNG."""
    if not (body.startswith(b"RIFF") and body[8:12] == b"WEBP"):
        return body
    from PIL import ImageOps

    if len(body) > MAX_IMAGE_BYTES:
        raise ValueError("Gallery image exceeds the encoded byte limit")
    try:
        with Image.open(BytesIO(body)) as image:
            if (
                getattr(image, "n_frames", 1) != 1
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Only bounded static WebP images can be indexed")
            image.load()
            pixels = ImageOps.exif_transpose(image)
            pixels.info.clear()
            stream = BytesIO()
            pixels.save(stream, format="PNG")
            return stream.getvalue()
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid gallery WebP image") from exc


@dataclass(frozen=True)
class Recipe:
    size: int = 256
    resize_shortest_edge: int = 256
    mean: tuple[float, float, float] = (0.0, 0.0, 0.0)
    std: tuple[float, float, float] = (1.0, 1.0, 1.0)
    version: str = RECIPE_VERSION

    def validate(self) -> None:
        if (
            self.version not in (LEGACY_RECIPE_VERSION, RECIPE_VERSION)
            or type(self.size) is not int
            or type(self.resize_shortest_edge) is not int
            or not 1 <= self.size <= self.resize_shortest_edge <= 2048
            or len(self.mean) != 3
            or len(self.std) != 3
            or any(
                type(x) not in {int, float} or abs(x) > 1e6 or not math.isfinite(x)
                for x in self.mean
            )
            or any(
                type(x) not in {int, float}
                or not 1e-6 <= x <= 1e6
                or not math.isfinite(x)
                for x in self.std
            )
        ):
            raise ValueError("Unsupported image preprocessing recipe")


def _tiff_orientation(body: bytes) -> int:
    if len(body) < 8 or body[:2] not in {b"II", b"MM"}:
        return 1
    order = "<" if body[:2] == b"II" else ">"
    if struct.unpack_from(order + "H", body, 2)[0] != 42:
        return 1
    offset = struct.unpack_from(order + "I", body, 4)[0]
    if offset > len(body) - 2:
        return 1
    count = struct.unpack_from(order + "H", body, offset)[0]
    for index in range(count):
        position = offset + 2 + index * 12
        if position > len(body) - 12:
            return 1
        tag, kind, size = struct.unpack_from(order + "HHI", body, position)
        if tag == 274 and kind == 3 and size == 1:
            value = struct.unpack_from(order + "H", body, position + 8)[0]
            return value if 1 <= value <= 8 else 1
    return 1


def _orientation(body: bytes) -> int:
    orientation = 1
    if body.startswith(_PNG):
        offset = 8
        while offset + 12 <= len(body):
            size = int.from_bytes(body[offset : offset + 4], "big")
            kind = body[offset + 4 : offset + 8]
            end = offset + 12 + size
            if end > len(body):
                raise ValueError("Invalid PNG image")
            value = body[offset + 8 : end - 4]
            if kind == b"IHDR" and (len(value) != 13 or value[8] > 8):
                raise ValueError(
                    "Only PNG images up to 8 bits per channel are supported"
                )
            if kind == b"acTL":
                raise ValueError("Animated PNG images are not supported")
            if kind == b"eXIf":
                orientation = _tiff_orientation(value)
            offset = end
            if kind == b"IEND":
                break
        return orientation
    if not body.startswith(b"\xff\xd8"):
        raise ValueError("Expected a JPEG or PNG image")
    offset = 2
    while offset < len(body):
        if body[offset] != 255:
            raise ValueError("Invalid JPEG image")
        while offset < len(body) and body[offset] == 255:
            offset += 1
        if offset >= len(body):
            break
        marker = body[offset]
        offset += 1
        if marker in {0xDA, 0xD9}:
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(body):
            raise ValueError("Invalid JPEG image")
        size = int.from_bytes(body[offset : offset + 2], "big")
        if size < 2 or offset + size > len(body):
            raise ValueError("Invalid JPEG image")
        value = body[offset + 2 : offset + size]
        if marker == 0xE1 and value.startswith(b"Exif\0\0"):
            orientation = _tiff_orientation(value[6:])
        if marker == 0xE2 and value.startswith(b"MPF\0"):
            raise ValueError("Multi-picture JPEG images are not supported")
        offset += size
    return orientation


def _decode(body: bytes) -> Image.Image:
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the encoded byte limit or is empty")
    orientation = _orientation(body)
    try:
        with Image.open(BytesIO(body)) as original:
            if original.format not in {"PNG", "JPEG"}:
                raise ValueError("Expected a JPEG or PNG image")
            if original.width * original.height > MAX_IMAGE_PIXELS:
                raise ValueError("Image exceeds the decoded pixel limit")
            original.verify()
        with Image.open(BytesIO(body)) as original:
            original.load()
            if original.mode in {"RGBA", "LA", "P"} or "transparency" in original.info:
                rgba = original.convert("RGBA")
                decoded = Image.new("RGB", original.size, "white")
                decoded.paste(rgba, mask=rgba.getchannel("A"))
            else:
                decoded = original.convert("RGB")
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or truncated image") from exc
    transpose = {
        2: Image.Transpose.FLIP_LEFT_RIGHT,
        3: Image.Transpose.ROTATE_180,
        4: Image.Transpose.FLIP_TOP_BOTTOM,
        5: Image.Transpose.TRANSPOSE,
        6: Image.Transpose.ROTATE_270,
        7: Image.Transpose.TRANSVERSE,
        8: Image.Transpose.ROTATE_90,
    }
    return decoded.transpose(transpose[orientation]) if orientation != 1 else decoded


def _coefficients(
    source: int, target: int, start: int, count: int
) -> list[tuple[int, NDArray[np.int64]]]:
    scale = source / target
    support = max(1.0, scale)
    inverse = 1.0 / support
    result = []
    for coordinate in range(start, start + count):
        center = (coordinate + 0.5) * scale
        first = max(0, int(center - support + 0.5))
        end = min(source, int(center + support + 0.5))
        weights = [
            max(0.0, 1.0 - abs((i - center + 0.5) * inverse)) for i in range(first, end)
        ]
        total = sum(weights)
        fixed = np.array([int(0.5 + w / total * _PRECISION) for w in weights])
        result.append((first, fixed.astype(np.int64)))
    return result


def _resize_crop(image: Image.Image, recipe: Recipe) -> NDArray[np.uint8]:
    width, height = image.size
    shortest = min(width, height)
    resized_width = width * recipe.resize_shortest_edge // shortest
    resized_height = height * recipe.resize_shortest_edge // shortest
    left = round((resized_width - recipe.size) / 2)
    top = round((resized_height - recipe.size) / 2)
    horizontal = _coefficients(width, resized_width, left, recipe.size)
    vertical = _coefficients(height, resized_height, top, recipe.size)
    first_row = min(start for start, _ in vertical)
    last_row = max(start + len(weights) for start, weights in vertical)
    pixels = np.asarray(image)[first_row:last_row]
    intermediate = np.empty((last_row - first_row, recipe.size, 3), dtype=np.uint8)
    for x, (start, weights) in enumerate(horizontal):
        sums = np.einsum(
            "hwc,w->hc",
            pixels[:, start : start + len(weights)],
            weights,
            dtype=np.int64,
        )
        intermediate[:, x] = np.clip((sums + (_PRECISION >> 1)) >> 22, 0, 255)
    result = np.empty((recipe.size, recipe.size, 3), dtype=np.uint8)
    for y, (start, weights) in enumerate(vertical):
        offset = start - first_row
        sums = np.einsum(
            "hwc,h->wc",
            intermediate[offset : offset + len(weights)],
            weights,
            dtype=np.int64,
        )
        result[y] = np.clip((sums + (_PRECISION >> 1)) >> 22, 0, 255)
    return result


def preprocess(body: bytes, recipe: Recipe) -> NDArray[np.float32]:
    """Return contiguous CHW float32 pixels; no batch axis or network access."""
    recipe.validate()
    pixels = _resize_crop(_decode(body), recipe)
    result = pixels.transpose(2, 0, 1).astype(np.float32)
    result /= np.float32(255)
    result -= np.asarray(recipe.mean, dtype=np.float32)[:, None, None]
    result /= np.asarray(recipe.std, dtype=np.float32)[:, None, None]
    return np.ascontiguousarray(result)
