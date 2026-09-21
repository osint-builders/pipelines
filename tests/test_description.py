import copy
import hashlib
import json
from contextlib import nullcontext
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from pipelines import description
from pipelines.description import Analyzer


@pytest.fixture
def local_model(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, dict]:
    lock = Path(__file__).parents[1] / "src/pipelines/description_model.lock.json"
    manifest = json.loads(lock.read_text())
    for entry in manifest["files"]:
        body = entry["file"].encode()
        (tmp_path / entry["file"]).write_bytes(body)
        entry.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
    monkeypatch.setattr(description.importlib.metadata, "version", lambda _: "test-1")
    path = tmp_path / "description-model.json"
    path.write_text(json.dumps(manifest))
    return path, manifest


def test_recipe_available_without_model_loading_and_tracks_changes(
    local_model: tuple[Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, manifest = local_model
    analyzer = Analyzer.from_manifest(path)
    assert analyzer._model is None
    recipe = asdict(analyzer.recipe)
    assert str(path.parent) not in json.dumps(recipe)
    assert recipe["settings"]["runtime"]["device"] == "cpu"
    assert recipe["settings"]["files"] == sorted(
        manifest["files"], key=lambda x: x["file"]
    )
    manifest["prompt"] = "Describe visible shapes."
    changed = Analyzer(path.parent, manifest)
    assert changed.recipe != analyzer.recipe
    assert Analyzer.from_manifest(path, device="cuda").recipe != analyzer.recipe
    monkeypatch.setattr(description.importlib.metadata, "version", lambda _: "test-2")
    assert Analyzer.from_manifest(path).recipe != analyzer.recipe
    changed.recipe.settings["prompt"] = "external mutation"
    assert changed.recipe.settings["prompt"] == "Describe visible shapes."


def test_numpy_version_changes_recipe_without_device_ordinal(
    local_model: tuple[Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = local_model
    original = Analyzer.from_manifest(path, device="cuda").recipe
    assert Analyzer.from_manifest(path, device="cuda").recipe == original
    assert original.settings["runtime"]["packages"]["numpy"] == "test-1"
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "1")
    assert Analyzer.from_manifest(path, device="cuda").recipe == original
    monkeypatch.setattr(
        description.importlib.metadata,
        "version",
        lambda name: "test-2" if name == "numpy" else "test-1",
    )
    assert Analyzer.from_manifest(path, device="cuda").recipe != original


@pytest.mark.parametrize(
    "change",
    [
        {"schema_version": True},
        {"model_id": "other/model"},
        {"revision": "main"},
        {"preprocess": {"min_pixels": True, "max_pixels": 262144}},
        {"preprocess": {"min_pixels": 65536, "max_pixels": 65535}},
        {"generation": {"do_sample": True, "num_beams": 1, "max_new_tokens": 64}},
        {"generation": {"do_sample": False, "num_beams": True, "max_new_tokens": 64}},
        {"generation": {"do_sample": False, "num_beams": 1, "max_new_tokens": 10000}},
    ],
)
def test_rejects_incompatible_or_unbounded_recipe(
    local_model: tuple[Path, dict], change: dict
) -> None:
    path, manifest = local_model
    with pytest.raises(ValueError):
        Analyzer(path.parent, {**manifest, **change})


def test_rejects_corrupt_missing_duplicate_and_escaping_files(
    local_model: tuple[Path, dict],
) -> None:
    path, manifest = local_model
    for name in ("../model.safetensors", "C:/model.safetensors", "chat_template.json"):
        changed = copy.deepcopy(manifest)
        changed["files"][1]["file"] = name
        with pytest.raises(ValueError):
            Analyzer(path.parent, changed)
    body = path.parent / "model.safetensors"
    body.write_bytes(b"x" * body.stat().st_size)
    with pytest.raises(ValueError, match="checksum"):
        Analyzer.from_manifest(path)
    body.unlink()
    with pytest.raises(ValueError, match="size"):
        Analyzer.from_manifest(path)


def test_unpinned_processor_override_cannot_change_cached_recipe(
    local_model: tuple[Path, dict],
) -> None:
    path, _ = local_model
    (path.parent / "processor_config.json").write_text("{}")
    with pytest.raises(ValueError, match="Unpinned"):
        Analyzer.from_manifest(path)


def test_analyze_preserves_visible_pixels_and_never_invents_confidence(
    local_model: tuple[Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    path, _ = local_model
    seen: dict = {}

    class Tensor(np.ndarray):
        def to(self, _: object) -> "Tensor":
            return self

    class Inputs(dict):
        def to(self, _: object) -> "Inputs":
            return self

    class Processor:
        def apply_chat_template(self, messages: list, **kwargs: object) -> str:
            seen["messages"] = messages
            return "local prompt"

        def __call__(self, *, images: list, **kwargs: object) -> Inputs:
            seen["pixels"] = list(images[0].get_flattened_data())
            return Inputs(
                input_ids=np.array([[1, 2]]), pixel_values=np.zeros(1).view(Tensor)
            )

        def decode(self, tokens: np.ndarray, **kwargs: object) -> str:
            assert tokens.tolist() == [3, 4]
            return " A green\n rectangular panel.  "

    def load(analyzer: Analyzer) -> None:
        analyzer._processor = Processor()
        analyzer._model = SimpleNamespace(generate=lambda **_: np.array([[1, 2, 3, 4]]))
        analyzer._torch = SimpleNamespace(float32="float32", inference_mode=nullcontext)

    monkeypatch.setattr(Analyzer, "_load", load)
    image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
    image.putpixel((1, 0), (0, 200, 0, 255))
    stream = BytesIO()
    image.save(stream, format="PNG")
    result = Analyzer.from_manifest(path).analyze(stream.getvalue())
    assert result == {
        "text": "A green rectangular panel.",
        "confidence": None,
        "regions": [],
    }
    assert seen["pixels"] == [(255, 255, 255), (0, 200, 0)]
    assert seen["messages"][0]["content"][0] == {"type": "image"}


def test_invalid_media_rejected_before_loading_weights(
    local_model: tuple[Path, dict], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail_load(_: Analyzer) -> None:
        pytest.fail("Invalid image should not initialize the model")

    monkeypatch.setattr(Analyzer, "_load", fail_load)
    analyzer = Analyzer.from_manifest(local_model[0])
    with pytest.raises((ValueError, OSError)):
        analyzer.analyze(b"not an image")
    frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
    stream = BytesIO()
    frames[0].save(
        stream, format="WEBP", save_all=True, append_images=frames[1:], duration=100
    )
    with pytest.raises(ValueError, match="Animated"):
        analyzer.analyze(stream.getvalue())
