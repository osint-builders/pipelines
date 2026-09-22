import hashlib
import json
from pathlib import Path
from typing import Never

import numpy as np
import pytest
from benchmark_images import benchmark, validate_fixture


def seed(tmp_path: Path) -> tuple[Path, Path, dict, dict[bytes, list[float]]]:
    media: list[dict] = []
    vectors: dict[bytes, list[float]] = {}
    entries: list[tuple[str, str, list[str], list[float]]] = [
        ("target-side", "gallery", ["a:target"], [0.6, 0.8]),
        ("target-front", "gallery", ["a:target"], [0.8, 0.6]),
        ("distractor", "gallery", ["b:other"], [1, 0]),
        ("other", "gallery", ["a:other"], [-1, 0]),
        ("query", "development", ["a:target"], [1, 0]),
        ("missing", "development", ["a:absent"], [1, 0]),
        ("negative", "development", [], [0, 1]),
        ("heldout", "evaluation", ["a:target"], [0, 1]),
    ]
    for name, split, entities, vector in entries:
        body = name.encode()
        (tmp_path / name).write_bytes(body)
        vectors[body] = vector
        media.append(
            {
                "id": name,
                "split": split,
                "photo_group": name,
                "entity_ids": entities,
                "status": "captured",
                "bytes": len(body),
                "local_path": name,
                "sha256": hashlib.sha256(body).hexdigest(),
            }
        )
    cases = [
        {
            "id": item["id"],
            "split": item["split"],
            "status": "ready",
            "task": "photograph" if item["entity_ids"] else "no_match",
            "query": {"image_id": item["id"], "source": "a"},
            "expected_ids": item["entity_ids"],
        }
        for item in media
        if item["split"] != "gallery"
    ]
    cases += [
        {
            "id": "pending",
            "split": "development",
            "status": "pending_independent_gallery",
            "task": "diagram",
            "query": {"image_id": "query"},
            "expected_ids": ["a:target"],
        },
        {
            "id": "mixed",
            "split": "development",
            "status": "ready",
            "task": "image_text",
            "query": {"image_id": "query", "text": "target"},
            "expected_ids": ["a:target"],
        },
        {
            "id": "text",
            "split": "development",
            "status": "ready",
            "task": "exact_designation",
            "query": {"text": "target"},
            "expected_ids": ["a:target"],
        },
    ]
    fixture = {"media": media, "cases": cases}
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    body = b"fake model"
    (tmp_path / "model.onnx").write_bytes(body)
    manifest_path = tmp_path / "model.json"
    manifest_path.write_text(
        json.dumps(
            {
                "file": "model.onnx",
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "dimensions": 2,
            }
        ),
        encoding="utf-8",
    )
    return fixture_path, manifest_path, fixture, vectors


def test_seed_ranking_groups_views_keeps_misses_and_separates_source_scope(
    tmp_path: Path,
) -> None:
    fixture_path, manifest, _, vectors = seed(tmp_path)
    encoded = []

    class FakeEncoder:
        def encode(self, body: bytes) -> np.ndarray:
            encoded.append(body)
            return np.array(vectors[body], dtype=np.float32)

    report = benchmark(
        [manifest],
        fixture_path,
        "development",
        root=tmp_path,
        encoder_factory=lambda *_: FakeEncoder(),
    )
    assert b"heldout" not in encoded
    assert report["gallery_entities"] == 3
    assert report["negative_queries"] == 1
    assert report["abstention"]["false_acceptance"] is None
    assert {row["id"] for row in report["excluded"]} == {"pending", "mixed", "text"}
    for representation in report["models"][0]["representations"].values():
        query = representation["cases"][0]
        assert query["global"]["rank"] == 2
        assert [row["id"] for row in query["global"]["results"]] == [
            "b:other",
            "a:target",
            "a:other",
        ]
        assert query["source_filtered"]["rank"] == 1
        assert query["source_filtered"]["results"][0]["media_id"] == "target-front"
        assert representation["cases"][1]["global"]["rank"] is None
        quality = representation["metrics"]["source_filtered"]["overall"]
        assert quality["unique_photo_groups"] == 2
        assert quality["top1"]["numerator"] == 1
        assert quality["top1"]["denominator"] == 2
        assert quality["top1"]["wilson95"][0] < 0.5 < quality["top1"]["wilson95"][1]
    assert len(report["fixture_sha256"]) == len(report["gallery_sha256"]) == 64


@pytest.mark.parametrize("leak", ["photo_group", "sha256", "derivative"])
def test_leakage_in_unselected_mixed_or_pending_queries_is_rejected(
    tmp_path: Path, leak: str
) -> None:
    fixture_path, manifest, fixture, _ = seed(tmp_path)
    heldout = fixture["media"][-1]
    if leak == "derivative":
        heldout["crop_from"] = fixture["media"][0]["id"]
    else:
        heldout[leak] = fixture["media"][0][leak]
    fixture["cases"][3]["status"] = "pending_independent_gallery"
    fixture["cases"][3]["query"]["text"] = "unselected mixed query"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    with pytest.raises(ValueError, match="leakage"):
        benchmark([manifest], fixture_path, "development", root=tmp_path)


@pytest.mark.parametrize("file", ["target-side", "query", "model.onnx"])
def test_changed_cached_bytes_fail_before_encoder_construction(
    tmp_path: Path, file: str
) -> None:
    fixture_path, manifest, _, _ = seed(tmp_path)
    target = tmp_path / file
    target.write_bytes(b"x" * target.stat().st_size)

    def unexpected(*_: object) -> Never:
        raise AssertionError("No model should be constructed with unverified inputs")

    with pytest.raises(ValueError, match="checksum/size"):
        benchmark(
            [manifest],
            fixture_path,
            "development",
            root=tmp_path,
            encoder_factory=unexpected,
        )


def test_query_cannot_be_relabelled_as_gallery(tmp_path: Path) -> None:
    _, _, fixture, _ = seed(tmp_path)
    fixture["media"][-1]["split"] = "gallery"
    with pytest.raises(ValueError, match="Query/gallery"):
        validate_fixture(fixture)


def test_evaluation_requires_matching_frozen_development_selection(
    tmp_path: Path,
) -> None:
    fixture_path, manifest, _, vectors = seed(tmp_path)
    constructed = []

    class FakeEncoder:
        def encode(self, body: bytes) -> np.ndarray:
            return np.array(vectors[body], dtype=np.float32)

    def factory(*_: object) -> FakeEncoder:
        constructed.append(True)
        return FakeEncoder()

    development = benchmark(
        [manifest], fixture_path, "development", root=tmp_path, encoder_factory=factory
    )
    frozen = {
        "schema_version": 1,
        "selection_split": "development",
        "fixture_sha256": development["fixture_sha256"],
        "gallery_sha256": development["gallery_sha256"],
        "selected_model_sha256": development["models"][0]["model_sha256"],
    }
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(frozen), encoding="utf-8")
    evaluation = benchmark(
        [manifest],
        fixture_path,
        "evaluation",
        selection=selection,
        root=tmp_path,
        encoder_factory=factory,
    )
    assert evaluation["selection"] == frozen
    assert (
        evaluation["selection_sha256"]
        == hashlib.sha256(selection.read_bytes()).hexdigest()
    )
    constructed.clear()
    with pytest.raises(ValueError, match="requires a frozen"):
        benchmark(
            [manifest],
            fixture_path,
            "evaluation",
            root=tmp_path,
            encoder_factory=factory,
        )
    for key in (
        "selection_split",
        "fixture_sha256",
        "gallery_sha256",
        "selected_model_sha256",
    ):
        selection.write_text(json.dumps({**frozen, key: "mismatch"}), encoding="utf-8")
        with pytest.raises(ValueError, match="Frozen selection"):
            benchmark(
                [manifest],
                fixture_path,
                "evaluation",
                selection=selection,
                root=tmp_path,
                encoder_factory=factory,
            )
    assert not constructed
