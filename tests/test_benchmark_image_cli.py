import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from benchmark_image_cli import (
    benchmark,
    gallery_contract,
    summarize,
    validate_response,
)
from test_benchmark_images import seed


def archive_for(fixture: dict, *, include_query: bool = False) -> bytes:
    pictures = [item for item in fixture["media"] if item["split"] == "gallery"]
    if include_query:
        pictures.append(
            next(item for item in fixture["media"] if item["id"] == "query")
        )
    rows: list[dict] = [
        {
            "id": item["id"],
            "sha256": item["sha256"],
            "vector_index": index,
            "references": [
                {"entity_id": entity, "evidence_id": "page"}
                for entity in item["entity_ids"]
            ],
        }
        for index, item in enumerate(pictures)
    ]
    raw = json.dumps(rows).encode()
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("image/index.json", raw)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "dataset_id": "test",
                    "image": {
                        "gallery_sha256": hashlib.sha256(raw).hexdigest(),
                        "model_sha256": "a" * 64,
                        "search": {"calibration": None},
                    },
                }
            ),
        )
    return body.getvalue()


def test_broad_capture_cannot_be_reported_as_held_out_cli_quality(
    tmp_path: Path,
) -> None:
    fixture_path, _, fixture, _ = seed(tmp_path)
    with zipfile.ZipFile(io.BytesIO(archive_for(fixture))) as archive:
        gallery_contract(archive, fixture)
    with zipfile.ZipFile(
        io.BytesIO(archive_for(fixture, include_query=True))
    ) as archive:
        with pytest.raises(ValueError, match="only the frozen gallery"):
            gallery_contract(archive, fixture)
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(archive_for(fixture))
    with pytest.raises(ValueError, match="frozen development selection"):
        benchmark(
            tmp_path / "binary", bundle, fixture_path, "evaluation", root=tmp_path
        )


def test_ranked_suggestions_do_not_inflate_accepted_quality() -> None:
    rows: list[dict] = [
        {
            "expected_ids": ["a:one"],
            "rank": 1,
            "photo_group": "one",
            "match_status": "no_supported_match",
        },
        {
            "expected_ids": ["a:two"],
            "rank": 2,
            "photo_group": "two",
            "match_status": "candidates",
        },
        {
            "expected_ids": [],
            "rank": None,
            "photo_group": "sky",
            "match_status": "candidates",
        },
    ]
    result = summarize(rows)
    assert result["ranked_top1"]["numerator"] == 1
    assert result["accepted_top1"]["numerator"] == 0
    assert result["ranked_recall_at_5"]["rate"] == 1
    assert result["accepted_recall_at_5"]["rate"] == 0.5
    assert result["false_acceptance"]["rate"] == 1


@pytest.mark.parametrize(
    "failure", ["duplicate", "filter", "text_cosine", "association"]
)
def test_invalid_cli_results_cannot_be_scored(failure: str) -> None:
    record = {
        "vector_index": 0,
        "references": [{"entity_id": "a:one", "evidence_id": "page"}],
    }
    item: dict = {
        "id": "a:one",
        "source": "a",
        "cosine": None,
        "name_match": False,
        "score": 0.5,
        "matches": [
            {
                "channel": "image",
                "media_id": "media",
                "evidence_id": "page",
                "score": 0.5,
                "url": "https://source.test/page",
                "model_sha256": "a" * 64,
            }
        ],
    }
    response = {
        "dataset_id": "bundle",
        "query_image_sha256": "hash",
        "query_type": "image",
        "match_status": "no_supported_match",
        "calibration_status": "uncalibrated",
        "results": [item],
    }
    manifest = {
        "dataset_id": "bundle",
        "image": {"model_sha256": "a" * 64, "search": {"calibration": None}},
    }
    validate_response(
        response, manifest, {"media": record}, {"sha256": "hash"}, "a", False
    )
    if failure == "duplicate":
        response["results"] = [item, item]
    elif failure == "filter":
        item["source"] = "b"
    elif failure == "text_cosine":
        item["cosine"] = 0.9
    else:
        record["references"] = [{"entity_id": "a:other", "evidence_id": "page"}]
    with pytest.raises(ValueError):
        validate_response(
            response,
            manifest,
            {"media": record},
            {"sha256": "hash"},
            "a",
            False,
        )


@pytest.mark.parametrize("frozen_hash", [None, "b" * 64])
def test_evaluation_freezes_executable_before_launch(
    tmp_path: Path, frozen_hash: str | None
) -> None:
    fixture_path, _, fixture, _ = seed(tmp_path)
    bundle = tmp_path / "bundle.zip"
    bundle.write_bytes(archive_for(fixture))
    binary = tmp_path / "binary"
    binary.write_bytes(b"new ranking code")
    with zipfile.ZipFile(bundle) as archive:
        manifest, _ = gallery_contract(archive, fixture)
    frozen: dict = {
        "selection_split": "development",
        "identity": {
            "fixture_sha256": hashlib.sha256(fixture_path.read_bytes()).hexdigest(),
            "dataset_id": manifest["dataset_id"],
            "gallery_sha256": manifest["image"]["gallery_sha256"],
            "model_sha256": manifest["image"]["model_sha256"],
            "search": manifest["image"]["search"],
        },
    }
    if frozen_hash:
        frozen["binary_sha256"] = frozen_hash
    selection = tmp_path / "selection.json"
    selection.write_text(json.dumps(frozen), encoding="utf-8")

    def never_run(*args: str) -> dict:
        pytest.fail("Changed/unfrozen ranking executable was launched")

    with pytest.raises(ValueError, match="executable"):
        benchmark(
            binary,
            bundle,
            fixture_path,
            "evaluation",
            selection=selection,
            root=tmp_path,
            runner=never_run,
        )
