"""Local, checksum-pinned OCR with traceable image regions."""

import hashlib
import importlib
import importlib.metadata
import json
import math
from copy import deepcopy
from numbers import Real
from pathlib import Path
from typing import Any

from pipelines.media import ProcessingRecipe

LOCK_PATH = Path(__file__).with_name("ocr_model.lock.json")


def _canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def _versions(expected: dict[str, str], *, required: bool) -> dict[str, str]:
    versions = {}
    for name, pinned in expected.items():
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            if required:
                raise ValueError(
                    f"OCR requires {name}=={pinned}; install the OCR extra"
                ) from None
            actual = pinned
        if actual != pinned:
            raise ValueError(f"OCR requires {name}=={pinned}; found {actual}")
        versions[name] = actual
    return versions


def verify_models(directory: Path, manifest: dict) -> None:
    for item in manifest["models"].values():
        path = (directory / item["file"]).resolve()
        if not path.is_relative_to(directory.resolve()):
            raise ValueError("OCR model escapes its directory")
        if not path.is_file() or path.stat().st_size != item["bytes"]:
            raise ValueError(f"Missing or invalid OCR model: {item['file']}")
        if hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
            raise ValueError(f"OCR model checksum mismatch: {item['file']}")


def _settings(value: object) -> dict:
    fields = {
        "minimum_confidence",
        "maximum_regions",
        "maximum_text_chars",
        "working_max_side",
        "intra_op_threads",
        "inter_op_threads",
    }
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("OCR settings must declare the complete processing recipe")
    confidence = value["minimum_confidence"]
    if (
        type(confidence) not in {int, float}
        or not math.isfinite(confidence)
        or not 0 <= confidence <= 1
    ):
        raise ValueError("Invalid OCR minimum confidence")
    limits = {
        "maximum_regions": (1, 256),
        "maximum_text_chars": (1, 16384),
        "working_max_side": (32, 4000),
        "intra_op_threads": (1, 32),
        "inter_op_threads": (1, 32),
    }
    for name, (low, high) in limits.items():
        if type(value[name]) is not int or not low <= value[name] <= high:
            raise ValueError(f"Invalid OCR setting: {name}")
    return deepcopy(value)


def _engine(directory: Path, manifest: dict) -> Any:
    _versions(manifest["dependencies"], required=True)
    verify_models(directory, manifest)
    rapidocr = importlib.import_module("rapidocr")
    enums = importlib.import_module("rapidocr.utils.typings")
    if not isinstance(rapidocr.__file__, str):
        raise ValueError("Cannot locate the OCR runtime configuration")
    config = Path(rapidocr.__file__).with_name("config.yaml").read_bytes()
    if hashlib.sha256(config).hexdigest() != manifest["default_config_sha256"]:
        raise ValueError("OCR default configuration checksum mismatch")
    settings = manifest["settings"]
    params = {
        "Global.text_score": settings["minimum_confidence"],
        "Global.max_side_len": settings["working_max_side"],
        "Global.log_level": "critical",
        "EngineConfig.onnxruntime.intra_op_num_threads": settings["intra_op_threads"],
        "EngineConfig.onnxruntime.inter_op_num_threads": settings["inter_op_threads"],
        "Rec.lang_type": enums.LangRec.CYRILLIC,
        "Rec.model_type": enums.ModelType.MOBILE,
        "Rec.ocr_version": enums.OCRVersion.PPOCRV5,
        **{
            kind.capitalize() + ".model_path": str((directory / item["file"]).resolve())
            for kind, item in manifest["models"].items()
        },
    }
    return rapidocr.RapidOCR(params=params)


class Analyzer:
    def __init__(self, directory: Path, manifest: dict, *, device: str = "cpu") -> None:
        lock = json.loads(LOCK_PATH.read_bytes())
        if device != "cpu":
            raise ValueError("This OCR profile supports CPU execution only")
        if (
            not isinstance(manifest, dict)
            or set(manifest) != set(lock)
            or _canonical(
                {key: value for key, value in manifest.items() if key != "settings"}
            )
            != _canonical(
                {key: value for key, value in lock.items() if key != "settings"}
            )
        ):
            raise ValueError("OCR manifest does not match the pinned model profile")
        self._manifest = deepcopy(manifest)
        self._manifest["settings"] = _settings(manifest["settings"])
        self._directory = directory.resolve()
        verify_models(self._directory, self._manifest)
        versions = _versions(manifest["dependencies"], required=False)
        self._recipe = ProcessingRecipe(
            version="rapidocr-regions-v1",
            model_id=manifest["id"],
            model_revision=hashlib.sha256(_canonical(manifest["models"])).hexdigest(),
            settings={
                "manifest": self._manifest,
                "dependencies": versions,
                "backend": "onnxruntime-cpu",
                "precision": "float32",
                "decode": "exif-white-jpeg-png-v1",
                "coordinates": "normalized-exif-oriented-original",
                "aggregate_confidence": None,
            },
        )
        self._runtime: Any = None

    @classmethod
    def from_manifest(cls, path: Path, *, device: str = "cpu") -> "Analyzer":
        return cls(
            path.parent, json.loads(path.read_text(encoding="utf-8")), device=device
        )

    @property
    def recipe(self) -> ProcessingRecipe:
        return deepcopy(self._recipe)

    def analyze(self, body: bytes) -> dict:
        import numpy as np

        from pipelines.image_preprocess import _decode

        image = _decode(body)
        if self._runtime is None:
            self._runtime = _engine(self._directory, self._manifest)
        result = self._runtime(np.asarray(image)[:, :, ::-1].copy())
        texts = [] if result.txts is None else list(result.txts)
        scores = [] if result.scores is None else list(result.scores)
        boxes = [] if result.boxes is None else list(result.boxes)
        if len(texts) != len(scores) or len(texts) != len(boxes) or len(texts) > 1000:
            raise ValueError("Invalid OCR output lengths")
        regions: list[dict] = []
        settings = self._manifest["settings"]
        for text, confidence, box in zip(texts, scores, boxes, strict=True):
            if (
                not isinstance(text, str)
                or isinstance(confidence, bool)
                or not isinstance(confidence, Real)
                or not math.isfinite(float(confidence))
                or not 0 <= float(confidence) <= 1
            ):
                raise ValueError("Invalid OCR text or recognition score")
            points = np.asarray(box)
            if (
                points.shape != (4, 2)
                or points.dtype.kind not in "fiu"
                or not np.isfinite(points).all()
            ):
                raise ValueError("Invalid OCR region polygon")
            polygon = []
            for x, y in points:
                if (
                    not -1e-6 <= x <= image.width + 1e-6
                    or not -1e-6 <= y <= image.height + 1e-6
                ):
                    raise ValueError("OCR polygon lies outside the original image")
                polygon.append(
                    [
                        round(min(1.0, max(0.0, float(x) / image.width)), 6),
                        round(min(1.0, max(0.0, float(y) / image.height)), 6),
                    ]
                )
            text = text.strip()
            if not text or confidence < settings["minimum_confidence"]:
                continue
            regions.append(
                {"text": text, "confidence": float(confidence), "polygon": polygon}
            )
        text = "\n".join(region["text"] for region in regions)
        if (
            len(regions) > settings["maximum_regions"]
            or len(text) > settings["maximum_text_chars"]
        ):
            raise ValueError("OCR output exceeds the observation limits")
        return {"text": text, "confidence": None, "regions": regions}
