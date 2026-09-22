"""Local, checksum-pinned visual descriptions kept separate from source facts."""

import copy
import hashlib
import importlib.metadata
import json
import re
from io import BytesIO
from pathlib import Path
from typing import Any, Self

from PIL import Image, ImageOps

from pipelines.media import MAX_IMAGE_BYTES, ProcessingRecipe, _decode

RECIPE_VERSION = "qwen3-vl-visible-features-v1"
_FILES = {
    "chat_template.json",
    "config.json",
    "generation_config.json",
    "merges.txt",
    "model.safetensors",
    "preprocessor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
    "video_preprocessor_config.json",
    "vocab.json",
}
_PACKAGES = (
    "torch",
    "torchvision",
    "transformers",
    "safetensors",
    "pillow",
    "numpy",
    "tokenizers",
    "huggingface-hub",
)


def validate_manifest(manifest: dict) -> None:
    """Validate the complete input recipe before opening any model file."""
    if (
        type(manifest) is not dict
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or manifest.get("version") != RECIPE_VERSION
        or manifest.get("model_id") != "Qwen/Qwen3-VL-2B-Instruct"
        or not isinstance(manifest.get("revision"), str)
        or re.fullmatch(r"[0-9a-f]{40}", manifest["revision"]) is None
        or not isinstance(manifest.get("prompt"), str)
        or not 1 <= len(manifest["prompt"].strip()) <= 4096
    ):
        raise ValueError("Unsupported description model manifest")
    files = manifest.get("files")
    if not isinstance(files, list) or len(files) != len(_FILES):
        raise ValueError("Description model requires a complete file manifest")
    names = set()
    for entry in files:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"file", "bytes", "sha256"}
            or not isinstance(entry.get("file"), str)
            or entry["file"] not in _FILES
            or entry["file"] in names
            or type(entry.get("bytes")) is not int
            or not 1 <= entry["bytes"] <= 8 * 1024**3
            or not isinstance(entry.get("sha256"), str)
            or re.fullmatch(r"[a-f0-9]{64}", entry["sha256"]) is None
        ):
            raise ValueError("Invalid description model file")
        names.add(entry["file"])
    settings = manifest.get("preprocess")
    if (
        not isinstance(settings, dict)
        or set(settings) != {"min_pixels", "max_pixels"}
        or any(type(value) is not int for value in settings.values())
        or not 1024 <= settings["min_pixels"] <= settings["max_pixels"] <= 1024**2
    ):
        raise ValueError("Invalid description image resolution")
    generation = manifest.get("generation")
    if (
        not isinstance(generation, dict)
        or set(generation) != {"do_sample", "num_beams", "max_new_tokens"}
        or generation["do_sample"] is not False
        or type(generation["num_beams"]) is not int
        or generation["num_beams"] != 1
        or type(generation["max_new_tokens"]) is not int
        or not 1 <= generation["max_new_tokens"] <= 128
    ):
        raise ValueError("Description generation must be bounded and greedy")


def verify_files(directory: Path, manifest: dict) -> None:
    directory = directory.resolve()
    allowed = _FILES | {"description-model.json", "artifact-manifest.json"}
    if any(path.name not in allowed for path in directory.iterdir()):
        raise ValueError("Unpinned file in description model directory")
    for entry in manifest["files"]:
        path = (directory / entry["file"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Description model file escapes directory")
        if not path.is_file() or path.stat().st_size != entry["bytes"]:
            raise ValueError(f"Description model size mismatch: {entry['file']}")
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != entry["sha256"]:
            raise ValueError(f"Description model checksum mismatch: {entry['file']}")


def _picture(body: bytes) -> Image.Image:
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise ValueError("Image exceeds the encoded byte limit or is empty")
    with Image.open(BytesIO(body)) as image:
        content_type = Image.MIME.get(image.format or "", "")
    _decode(body, content_type)
    with Image.open(BytesIO(body)) as image:
        rgba = ImageOps.exif_transpose(image).convert("RGBA")
        result = Image.new("RGB", rgba.size, "white")
        result.paste(rgba, mask=rgba.getchannel("A"))
        return result


class Analyzer:
    def __init__(self, directory: Path, manifest: dict, *, device: str = "cpu") -> None:
        validate_manifest(manifest)
        if device not in {"cpu", "cuda"}:
            raise ValueError("Description device must be cpu or cuda")
        verify_files(directory, manifest)
        self._directory = directory.resolve()
        self._manifest = copy.deepcopy(manifest)
        self._device = device
        self._model: Any = None
        self._processor: Any = None
        self._torch: Any = None
        try:
            versions = {name: importlib.metadata.version(name) for name in _PACKAGES}
        except importlib.metadata.PackageNotFoundError as error:
            raise ValueError(
                "Install the description extra to analyze images"
            ) from error
        self._recipe = ProcessingRecipe(
            version=RECIPE_VERSION,
            model_id=manifest["model_id"],
            model_revision=manifest["revision"],
            settings={
                "files": sorted(
                    copy.deepcopy(manifest["files"]), key=lambda x: x["file"]
                ),
                "prompt": manifest["prompt"],
                "generation": copy.deepcopy(manifest["generation"]),
                "preprocess": {
                    **manifest["preprocess"],
                    "backend": "pil",
                    "decode": "bounded-media-exif-white-rgb-v1",
                },
                "runtime": {
                    "packages": versions,
                    "device": device,
                    "dtype": "float32" if device == "cpu" else "bfloat16",
                    "attention": "sdpa",
                    "torch_threads": 4,
                },
            },
        )

    @classmethod
    def from_manifest(cls, path: Path, *, device: str = "cpu") -> Self:
        return cls(
            path.parent, json.loads(path.read_text(encoding="utf-8")), device=device
        )

    @property
    def recipe(self) -> ProcessingRecipe:
        return copy.deepcopy(self._recipe)

    def _load(self) -> None:
        if self._model is not None:
            return
        import torch
        from transformers import (
            AutoImageProcessor,
            AutoModelForImageTextToText,
            AutoProcessor,
        )

        if self._device == "cuda" and (
            not torch.cuda.is_available() or not torch.cuda.is_bf16_supported()
        ):
            raise ValueError("Description CUDA inference requires a BF16-capable GPU")
        verify_files(self._directory, self._manifest)
        torch.set_num_threads(4)
        dtype = torch.float32 if self._device == "cpu" else torch.bfloat16
        common = {"local_files_only": True, "trust_remote_code": False}
        processor = AutoProcessor.from_pretrained(str(self._directory), **common)
        # AutoProcessor also forwards backend to a video processor with a read-only property.
        processor.image_processor = AutoImageProcessor.from_pretrained(
            str(self._directory),
            **common,
            backend="pil",
            **self._manifest["preprocess"],
        )
        model: torch.nn.Module = AutoModelForImageTextToText.from_pretrained(
            str(self._directory),
            **common,
            use_safetensors=True,
            dtype=dtype,
            attn_implementation="sdpa",
        )
        model = model.to(self._device).eval()
        self._processor, self._model, self._torch = processor, model, torch

    def analyze(self, body: bytes) -> dict:
        picture = _picture(body)
        self._load()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image"},
                    {"type": "text", "text": self._manifest["prompt"]},
                ],
            }
        ]
        prompt = self._processor.apply_chat_template(
            messages, add_generation_prompt=True, tokenize=False
        )
        inputs = self._processor(text=prompt, images=[picture], return_tensors="pt")
        inputs = inputs.to(self._device)
        dtype = self._torch.float32 if self._device == "cpu" else self._torch.bfloat16
        inputs["pixel_values"] = inputs["pixel_values"].to(dtype)
        with self._torch.inference_mode():
            generated = self._model.generate(**inputs, **self._manifest["generation"])
        tokens = generated[0, inputs["input_ids"].shape[-1] :]
        text = self._processor.decode(tokens, skip_special_tokens=True)
        if not isinstance(text, str) or len(text) > 16384:
            raise ValueError("Invalid description output")
        return {"text": " ".join(text.split()), "confidence": None, "regions": []}
