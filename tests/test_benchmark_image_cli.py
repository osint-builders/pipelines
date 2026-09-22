import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from benchmark_image_cli import (
    add_text_constraints,
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


@pytest.mark.parametrize("query_caption", [False, True])
def test_caption_gallery_excludes_held_out_media(
    tmp_path: Path, query_caption: bool
) -> None:
    _, _, fixture, _ = seed(tmp_path)
    with zipfile.ZipFile(io.BytesIO(archive_for(fixture))) as original:
        members = {name: original.read(name) for name in original.namelist()}
    manifest = json.loads(members["manifest.json"])
    gallery = json.loads(members["image/index.json"])
    captions = json.dumps(
        [{"media_id": "query" if query_caption else gallery[0]["id"]}]
    ).encode()
    manifest["search"] = {"version": "bm25-minilm-v1"}
    manifest["files"] = {"search/captions.json": hashlib.sha256(captions).hexdigest()}
    members["manifest.json"] = json.dumps(manifest).encode()
    members["search/captions.json"] = captions
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as output:
        for name, value in members.items():
            output.writestr(name, value)
    with zipfile.ZipFile(body) as archive:
        if query_caption:
            with pytest.raises(ValueError, match="captions must belong"):
                gallery_contract(archive, fixture)
        else:
            gallery_contract(archive, fixture)


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


def constraint_fixture(tmp_path: Path) -> tuple[Path, Path, Path]:
    fixture_path, _, fixture, _ = seed(tmp_path)
    with zipfile.ZipFile(io.BytesIO(archive_for(fixture))) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    member = "entities/a/target.json"
    members[member] = json.dumps(
        {"evidence": [{"id": "page", "markdown": "This is a portable radar."}]}
    ).encode()
    manifest["files"] = {member: hashlib.sha256(members[member]).hexdigest()}
    manifest["content_sha256"] = "source-corpus"
    members["manifest.json"] = json.dumps(manifest).encode()
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, value in members.items():
            archive.writestr(name, value)
    constraints = tmp_path / "constraints.json"
    constraints.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "development_frozen_before_retrieval",
                "base_fixture_sha256": hashlib.sha256(
                    fixture_path.read_bytes()
                ).hexdigest(),
                "source_content_sha256": "source-corpus",
                "limitations": ["Development constraints."],
                "cases": [
                    {
                        "case_id": "query",
                        "text": "portable radar",
                        "evidence": {
                            "entity_id": "a:target",
                            "evidence_id": "page",
                            "quote": "portable radar",
                        },
                    }
                ],
            }
        )
    )
    return fixture_path, bundle, constraints


@pytest.mark.parametrize(
    "failure",
    [
        "fixture_hash",
        "corpus",
        "evaluation",
        "pending",
        "mixed",
        "empty",
        "long",
        "foreign_entity",
        "quote",
        "page",
        "duplicate",
        "checksum",
    ],
)
def test_constraints_reject_unfrozen_or_unsupported_text(
    tmp_path: Path, failure: str
) -> None:
    fixture_path, bundle, constraints_path = constraint_fixture(tmp_path)
    fixture = json.loads(fixture_path.read_bytes())
    constraints = json.loads(constraints_path.read_bytes())
    row = constraints["cases"][0]
    if failure == "fixture_hash":
        constraints["base_fixture_sha256"] = "changed"
    elif failure == "corpus":
        constraints["source_content_sha256"] = "changed"
    elif failure in {"evaluation", "pending", "mixed"}:
        row["case_id"] = "heldout" if failure == "evaluation" else failure
    elif failure == "empty":
        row["text"] = " "
    elif failure == "long":
        row["text"] = "x" * 1001
    elif failure == "foreign_entity":
        row["evidence"]["entity_id"] = "b:other"
    elif failure == "quote":
        row["evidence"]["quote"] = "unsupported specification"
    elif failure == "page":
        row["evidence"]["evidence_id"] = "different-page"
    elif failure == "duplicate":
        constraints["cases"].append(row)
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if failure == "checksum":
            manifest["files"]["entities/a/target.json"] = "changed"
        with pytest.raises(ValueError):
            add_text_constraints(
                fixture,
                fixture_path.read_bytes(),
                constraints,
                archive,
                manifest,
            )


def test_paired_development_constraints_preserve_fixture_and_report_identity(
    tmp_path: Path,
) -> None:
    fixture_path, bundle, constraints_path = constraint_fixture(tmp_path)
    original = fixture_path.read_bytes()
    calls = []

    def run(*args: str) -> dict:
        calls.append(args)
        if args == ("info",):
            return {"dataset_id": "test"}
        image_path = Path(args[args.index("--image") + 1])
        return {
            "dataset_id": "test",
            "query_image_sha256": hashlib.sha256(image_path.read_bytes()).hexdigest(),
            "query_type": "image_text"
            if args[-1] in {"target", "portable radar"}
            else "image",
            "match_status": "no_supported_match",
            "calibration_status": "uncalibrated",
            "results": [],
        }

    report = benchmark(
        tmp_path / "binary",
        bundle,
        fixture_path,
        "development",
        text_constraints=constraints_path,
        root=tmp_path,
        runner=run,
    )
    assert fixture_path.read_bytes() == original
    assert report["identity"]["fixture_sha256"] == hashlib.sha256(original).hexdigest()
    assert (
        report["identity"]["text_constraints_sha256"]
        == hashlib.sha256(constraints_path.read_bytes()).hexdigest()
    )
    paired = [row for row in report["cases"] if row["id"] == "query-text"]
    assert len(paired) == 2
    assert {row["scope"] for row in paired} == {"global", "source_filtered"}
    assert all(
        row["mode"] == "image_text"
        and row["photo_group"] == "query"
        and row["expected_ids"] == ["a:target"]
        for row in paired
    )
    assert len([command for command in calls if command[-1] == "portable radar"]) == 2
    assert (
        len(
            [
                row
                for row in report["cases"]
                if row["id"] == "query" and row["mode"] == "image"
            ]
        )
        == 2
    )
    assert "Development constraints." in report["limitations"]
    calls.clear()
    with pytest.raises(ValueError, match="development only"):
        benchmark(
            tmp_path / "binary",
            bundle,
            fixture_path,
            "evaluation",
            text_constraints=constraints_path,
            root=tmp_path,
            runner=run,
        )
    assert not calls


@pytest.mark.parametrize("stored_crlf", [False, True])
@pytest.mark.parametrize("checkout_crlf", [False, True])
def test_constraint_base_hash_allows_only_checkout_line_ending_changes(
    tmp_path: Path, stored_crlf: bool, checkout_crlf: bool
) -> None:
    fixture_path, bundle, constraints_path = constraint_fixture(tmp_path)
    fixture = json.loads(fixture_path.read_bytes())
    lf = (json.dumps(fixture, indent=2) + "\n").encode()
    stored = lf.replace(b"\n", b"\r\n") if stored_crlf else lf
    checkout = lf.replace(b"\n", b"\r\n") if checkout_crlf else lf
    constraints = json.loads(constraints_path.read_bytes())
    constraints["base_fixture_sha256"] = hashlib.sha256(stored).hexdigest()
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        combined = add_text_constraints(
            fixture, checkout, constraints, archive, manifest
        )
        assert len(combined["cases"]) == len(fixture["cases"]) + 1
        changed = checkout.replace(b'"query"', b'"altered-query"', 1)
        with pytest.raises(ValueError, match="frozen development corpus"):
            add_text_constraints(
                json.loads(changed), changed, constraints, archive, manifest
            )
