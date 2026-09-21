import hashlib
import importlib.metadata
import json
from collections.abc import Callable
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import prepare_ocr_model
import pytest
from PIL import Image

from pipelines import ocr


@pytest.fixture
def model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lock = json.loads(ocr.LOCK_PATH.read_bytes())
    for kind, item in lock["models"].items():
        body = ("graph " + kind).encode()
        (tmp_path / item["file"]).write_bytes(body)
        item.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    path = tmp_path / "ocr.json"
    path.write_text(json.dumps(lock), encoding="utf-8")
    monkeypatch.setattr(ocr, "LOCK_PATH", path)
    monkeypatch.setattr(prepare_ocr_model, "LOCK_PATH", path)
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: lock["dependencies"][name]
    )
    return path


def encoded(*, orientation: int = 1, alpha: bool = False) -> bytes:
    image = Image.new(
        "RGBA" if alpha else "RGB", (20, 10), (255, 0, 0, 0) if alpha else (255, 0, 0)
    )
    exif = Image.Exif()
    exif[274] = orientation
    output = BytesIO()
    image.save(output, format="PNG", exif=exif)
    return output.getvalue()


def result(
    texts: list | None = None, scores: list | None = None, boxes: list | None = None
) -> SimpleNamespace:
    return SimpleNamespace(txts=texts, scores=scores, boxes=boxes)


def test_lazy_cached_construction_and_region_coordinates(
    model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = []

    def factory(
        directory: Path, manifest: dict
    ) -> Callable[[np.ndarray], SimpleNamespace]:
        calls.append("load")

        def runtime(image: np.ndarray) -> SimpleNamespace:
            assert image.shape == (20, 10, 3)
            assert image[0, 0].tolist() == [0, 0, 255]
            return result(
                [" 1Л122Е ", "noise"],
                [np.float32(0.95), 0.5],
                [
                    [[0, 0], [10, 0], [10, 20], [0, 20]],
                    [[0, 0], [1, 0], [1, 1], [0, 1]],
                ],
            )

        return runtime

    monkeypatch.setattr(ocr, "_engine", factory)
    analyzer = ocr.Analyzer.from_manifest(model)
    recipe = asdict(analyzer.recipe)
    assert calls == []
    assert recipe["settings"]["dependencies"]["rapidocr"] == "3.9.2"
    assert str(model.parent) not in json.dumps(recipe)
    output = analyzer.analyze(encoded(orientation=6))
    assert output == {
        "text": "1Л122Е",
        "confidence": None,
        "regions": [
            {
                "text": "1Л122Е",
                "confidence": float(np.float32(0.95)),
                "polygon": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
            }
        ],
    }
    assert calls == ["load"]
    assert analyzer.analyze(encoded(orientation=6)) == output
    assert calls == ["load"]
    analyzer.recipe.settings["backend"] = "changed"
    assert asdict(analyzer.recipe) == recipe


def test_alpha_and_empty_observation(
    model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def runtime(image: np.ndarray) -> SimpleNamespace:
        assert np.all(image == 255)
        return result()

    monkeypatch.setattr(ocr, "_engine", lambda *_: runtime)
    assert ocr.Analyzer.from_manifest(model).analyze(encoded(alpha=True)) == {
        "text": "",
        "confidence": None,
        "regions": [],
    }


@pytest.mark.parametrize(
    "failure",
    ["length", "nan", "boolean", "outside", "polygon", "blank", "too_many", "too_long"],
)
def test_invalid_outputs_fail_without_silent_truncation(
    model: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    box = [[0, 0], [20, 0], [20, 10], [0, 10]]
    output = result(["text"], [0.95], [box])
    if failure == "length":
        output.scores = []
    elif failure == "nan":
        output.scores = [float("nan")]
    elif failure == "boolean":
        output.scores = [True]
    elif failure == "outside":
        output.boxes[0][0][0] = -1
    elif failure == "polygon":
        output.boxes = [[[0, 0], [1, 1], [2, 2]]]
    elif failure == "blank":
        output.txts = [None]
    elif failure == "too_many":
        output = result(["text"] * 257, [0.95] * 257, [box] * 257)
    else:
        output.txts = ["x" * 16385]
    monkeypatch.setattr(ocr, "_engine", lambda *_: lambda image: output)
    with pytest.raises(ValueError):
        ocr.Analyzer.from_manifest(model).analyze(encoded())


def test_invalid_input_fails_before_model_load(
    model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: object) -> None:
        pytest.fail("Invalid image reached OCR runtime")

    monkeypatch.setattr(ocr, "_engine", forbidden)
    with pytest.raises(ValueError):
        ocr.Analyzer.from_manifest(model).analyze(b"https://example.test/not-an-image")


def test_dependency_mismatch_missing_runtime_and_cpu_scope(
    model: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with pytest.raises(ValueError, match="CPU"):
        ocr.Analyzer.from_manifest(model, device="cuda")
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.0")
    with pytest.raises(ValueError, match="requires"):
        ocr.Analyzer.from_manifest(model)

    def absent(name: str) -> str:
        raise importlib.metadata.PackageNotFoundError(name)

    monkeypatch.setattr(importlib.metadata, "version", absent)
    analyzer = ocr.Analyzer.from_manifest(model)
    assert analyzer.recipe.model_id
    with pytest.raises(ValueError, match="install the OCR extra"):
        analyzer.analyze(encoded())


def test_model_integrity_and_processing_settings_identity(model: Path) -> None:
    first = ocr.Analyzer.from_manifest(model)
    manifest = json.loads(model.read_bytes())
    manifest["settings"]["minimum_confidence"] = 0.9
    second = ocr.Analyzer(model.parent, manifest)
    assert asdict(first.recipe) != asdict(second.recipe)
    manifest["settings"]["minimum_confidence"] = True
    with pytest.raises(ValueError, match="confidence"):
        ocr.Analyzer(model.parent, manifest)
    manifest = json.loads(model.read_bytes())
    manifest["schema_version"] = True
    with pytest.raises(ValueError, match="pinned"):
        ocr.Analyzer(model.parent, manifest)
    manifest = json.loads(model.read_bytes())
    target = model.parent / manifest["models"]["det"]["file"]
    target.write_bytes(b"not valid")
    with pytest.raises(ValueError, match="OCR model"):
        ocr.Analyzer.from_manifest(model)


def test_prepare_existing_models_is_offline_and_download_checksums(
    model: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert prepare_ocr_model.prepare(model.parent) == model
    destination = tmp_path / "prepared"
    with pytest.raises(ValueError, match="--download"):
        prepare_ocr_model.prepare(destination)
    manifest = json.loads(model.read_bytes())
    bodies = {
        item["url"]: (model.parent / item["file"]).read_bytes()
        for item in manifest["models"].values()
    }
    monkeypatch.setattr(
        prepare_ocr_model.urllib.request,
        "urlopen",
        lambda url, timeout: BytesIO(bodies[url]),
    )
    prepared = prepare_ocr_model.prepare(destination, download=True)
    assert json.loads(prepared.read_bytes()) == manifest
    item = manifest["models"]["rec"]
    (destination / item["file"]).unlink()
    monkeypatch.setattr(
        prepare_ocr_model.urllib.request,
        "urlopen",
        lambda url, timeout: BytesIO(b"bad"),
    )
    with pytest.raises(ValueError, match="checksum"):
        prepare_ocr_model.prepare(destination, download=True)
    assert not (destination / item["file"]).with_suffix(".tmp").exists()
