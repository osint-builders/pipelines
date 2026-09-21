import json
import re
import zipfile
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from test_image_distribution import TextEncoder
from test_image_distribution import setup as setup
from test_observations import Gallery, analyze
from test_observations import gallery as gallery

from pipelines import distribution, observation_distribution, observations


class Tokenizer:
    def encode(self, text: str, add_special_tokens: bool = True) -> SimpleNamespace:
        matches = list(re.finditer(r"\S+", text))
        return SimpleNamespace(
            ids=list(range(len(matches) + (2 if add_special_tokens else 0))),
            offsets=[match.span() for match in matches],
        )


class Encoder(TextEncoder):
    calls: list[list[str]] = []

    def __init__(self, directory: Path) -> None:
        self.tokenizer = Tokenizer()

    def encode(self, texts: list[str]) -> bytes:
        self.calls.append(texts)
        return super().encode(texts)


@pytest.fixture
def text_model(gallery: Gallery, monkeypatch: pytest.MonkeyPatch) -> Path:
    path = gallery.root / "text-model"
    path.mkdir()
    body = b"fixture text model"
    (path / "model.onnx").write_bytes(body)
    lock = {
        "id": "fixture/text",
        "dimensions": 2,
        "max_tokens": 256,
        "files": {"model.onnx": {"sha256": observations.digest(body)}},
    }
    for module in [distribution, observation_distribution]:
        monkeypatch.setattr(module, "LOCK", lock)
        monkeypatch.setattr(module, "Encoder", Encoder)
    Encoder.calls = []
    return path


def build(gallery: Gallery, text_model: Path) -> tuple[dict[str, bytes], dict, dict]:
    _, analysis = analyze(gallery)
    members, metadata = observation_distribution.build_observation_members(
        gallery.root / "analysis.json",
        gallery.members,
        gallery.manifest["image"],
        text_model,
        gallery.root / "text-cache",
    )
    assert analysis["observations"] == json.loads(members["observations/index.json"])
    return members, metadata, analysis


def validate(
    gallery: Gallery, members: dict[str, bytes], metadata: dict, text_model: Path
) -> dict:
    combined = {
        **gallery.members,
        **members,
        "model/model.onnx": (text_model / "model.onnx").read_bytes(),
    }
    manifest = {
        **gallery.manifest,
        "format_version": 4,
        "observations": metadata,
        "files": {name: observations.digest(body) for name, body in combined.items()},
    }
    path = gallery.root / "observation-bundle.zip"
    distribution.write_bundle(path, combined)
    with zipfile.ZipFile(path) as archive:
        return observation_distribution.validate_observation_bundle(archive, manifest)


def test_embeddings_reuse_cache_and_preserve_generated_provenance(
    gallery: Gallery, text_model: Path
) -> None:
    first, metadata, analysis = build(gallery, text_model)
    assert metadata["records"] == metadata["chunks"] == 2
    assert metadata["vector_dtype"] == "float32-le"
    assert metadata["search"]["calibration"] is None
    assert validate(gallery, first, metadata, text_model) == metadata
    text = analysis["observations"][0]["text"]
    assert sum(batch.count(text) for batch in Encoder.calls) == 1
    second, next_metadata, _ = build(gallery, text_model)
    assert (first, metadata) == (second, next_metadata)
    assert sum(batch.count(text) for batch in Encoder.calls) == 1
    rows = json.loads(first["observations/index.json"])
    assert all(row["origin"] == "generated" for row in rows)
    assert all(
        ref["evidence_id"] == gallery.entities[0]["evidence"][0]["id"]
        for row in rows
        for ref in row["references"]
    )


def test_chunking_is_overlapping_bounded_and_has_no_source_title_prefix(
    text_model: Path,
) -> None:
    text = " ".join(f"term{index}" for index in range(500))
    chunks = observation_distribution.text_chunks(Encoder(text_model), text)  # type: ignore[arg-type]
    assert [len(chunk.split()) for chunk in chunks] == [192, 192, 192, 68]
    assert chunks[0].split()[-48:] == chunks[1].split()[:48]
    assert chunks[0].startswith("term0") and chunks[-1].endswith("term499")


@pytest.mark.parametrize(
    "mutation",
    [
        "vector_nan",
        "vector_norm",
        "vector_count",
        "chunk_owner",
        "chunk_text",
        "chunk_index",
        "missing_chunk",
        "model",
        "probe_nan",
        "probe_count",
        "probe_bool",
        "probe_duplicate",
        "probe_length",
        "recipe",
        "index",
        "foreign_gallery",
        "missing_ref",
        "orphan_member",
        "report",
    ],
)
def test_observation_bundle_rejects_corruption(
    gallery: Gallery, text_model: Path, mutation: str
) -> None:
    members, metadata, _ = build(gallery, text_model)
    if mutation.startswith("vector_"):
        vector = np.frombuffer(members["observations/vectors.f32"], "<f4").copy()
        vector[0] = np.nan if mutation == "vector_nan" else 5
        members["observations/vectors.f32"] = (
            vector.tobytes() if mutation != "vector_count" else b"a"
        )
    elif mutation.startswith("chunk_") or mutation == "missing_chunk":
        chunks = json.loads(members["observations/chunks.json"])
        if mutation == "chunk_owner":
            chunks[0]["observation_id"] = "f" * 64
        elif mutation == "chunk_text":
            chunks[0]["text"] = "Unrelated invented equipment name"
        elif mutation == "chunk_index":
            chunks[0]["vector_index"] = 1
        else:
            chunks.pop()
            metadata["chunks"] -= 1
            members["observations/vectors.f32"] = members["observations/vectors.f32"][
                :8
            ]
        members["observations/chunks.json"] = observations.canonical(chunks)
    elif mutation == "model":
        metadata["embedding_model_sha256"] = "f" * 64
    elif mutation.startswith("probe_"):
        probes = json.loads(members["observations/probes.json"])
        if mutation == "probe_count":
            probes.pop()
        elif mutation == "probe_duplicate":
            probes[1]["text"] = probes[0]["text"]
        elif mutation == "probe_length":
            probes[0]["text"] = "x" * 1001
        else:
            probes[0]["vector"][0] = True if mutation == "probe_bool" else float("nan")
        members["observations/probes.json"] = json.dumps(probes).encode()
    elif mutation == "recipe":
        recipes = json.loads(members["observations/recipes.json"])
        next(iter(recipes.values()))["model_revision"] = "changed"
        members["observations/recipes.json"] = observations.canonical(recipes)
    elif mutation == "index":
        metadata["index_sha256"] = "f" * 64
    elif mutation == "foreign_gallery":
        metadata["gallery_sha256"] = "f" * 64
    elif mutation == "missing_ref":
        rows = json.loads(members["observations/index.json"])
        rows[0]["references"].pop()
        members["observations/index.json"] = observations.canonical(rows)
        metadata["index_sha256"] = observations.digest(
            members["observations/index.json"]
        )
    elif mutation == "orphan_member":
        members["observations/unused.json"] = b"{}"
    else:
        report = json.loads(members["observations/report.json"])
        report["counts"] = {}
        members["observations/report.json"] = observations.canonical(report)
    with pytest.raises(ValueError):
        validate(gallery, members, metadata, text_model)


def test_corrupt_cached_vector_and_foreign_analysis_are_rejected(
    gallery: Gallery, text_model: Path
) -> None:
    build(gallery, text_model)
    cache = gallery.root / "text-cache"
    next(cache.glob("*.f32")).write_bytes(np.array([5.0, 0.0], dtype="<f4").tobytes())
    with pytest.raises(ValueError, match="normalized"):
        observation_distribution.build_observation_members(
            gallery.root / "analysis.json",
            gallery.members,
            gallery.manifest["image"],
            text_model,
            cache,
        )
    analysis = json.loads((gallery.root / "analysis.json").read_bytes())
    analysis["gallery_sha256"] = "e" * 64
    (gallery.root / "analysis.json").write_bytes(observations.canonical(analysis))
    with pytest.raises(ValueError, match="gallery"):
        observation_distribution.build_observation_members(
            gallery.root / "analysis.json",
            gallery.members,
            gallery.manifest["image"],
            text_model,
            cache,
        )


def test_format4_preserves_every_existing_source_and_image_member(
    gallery: Gallery, text_model: Path
) -> None:
    arguments = (
        gallery.root,
        [gallery.entities[0]["source"]],
        text_model,
        gallery.root / "package-cache",
    )
    first = distribution.package(
        *arguments, gallery.root / "format3.zip", image_model=gallery.model
    )
    analyze(gallery)
    second = distribution.package(
        *arguments,
        gallery.root / "format4.zip",
        image_model=gallery.model,
        observations=gallery.root / "analysis.json",
    )
    assert first["format_version"] == 3 and second["format_version"] == 4
    assert first["content_sha256"] == second["content_sha256"]
    assert first["dataset_id"] != second["dataset_id"]
    with (
        zipfile.ZipFile(gallery.root / "format3.zip") as original,
        zipfile.ZipFile(gallery.root / "format4.zip") as extended,
    ):
        for name in original.namelist():
            if name != "manifest.json":
                assert original.read(name) == extended.read(name)
        assert all(
            name.startswith("observations/")
            for name in set(extended.namelist()) - set(original.namelist())
        )
    assert (
        distribution.package(
            *arguments,
            gallery.root / "format4.zip",
            image_model=gallery.model,
            observations=gallery.root / "analysis.json",
        )["changed"]
        is False
    )
