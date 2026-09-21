import hashlib
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from benchmark_ranking import (
    benchmark,
    freeze_selection,
    sha256,
    summarize,
    validate_fixture,
    validate_response,
)


def make_bundle(path: Path, dataset_id: str) -> dict:
    entities: list[dict] = [
        {
            "id": f"source:{name}",
            "source": "source",
            "title": name,
            "aliases": [name.upper()],
            "url": f"https://example.test/{name}",
            "evidence": [
                {
                    "id": f"evidence-{name}",
                    "url": f"https://example.test/{name}",
                    "canonical_url": f"https://example.test/{name}/canonical",
                    "markdown": f"A source-backed {name} radar.",
                }
            ],
        }
        for name in ("one", "two", "other")
    ]
    members = {
        "index.json": json.dumps(
            [
                {key: value for key, value in entity.items() if key != "evidence"}
                for entity in entities
            ]
        ).encode(),
        "chunks.json": b"[]",
        "vectors.f32": b"",
        **{
            "entities/" + entity["id"].replace(":", "/") + ".json": json.dumps(
                entity
            ).encode()
            for entity in entities
        },
    }
    manifest = {
        "dataset_id": dataset_id,
        "recipe_sha256": "recipe-" + dataset_id,
        "content_sha256": "same-source",
        "sources": {"source": {}},
        "files": {
            name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()
        },
    }
    with zipfile.ZipFile(path, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
        archive.writestr("manifest.json", json.dumps(manifest))
    return manifest


def setup_benchmark(tmp_path: Path) -> tuple[Path, Path, Path, Path, Path]:
    before, after = tmp_path / "before.zip", tmp_path / "after.zip"
    manifest = make_bundle(before, "baseline")
    make_bundle(after, "candidate")
    cases = []
    for split, name in (("development", "one"), ("evaluation", "two")):
        cases.append(
            {
                "id": name,
                "split": split,
                "source": "source",
                "query": name,
                "task": "partial_name",
                "entity_group": name,
                "expected_ids": [f"source:{name}"],
                "confusable_ids": [],
                "evidence": [
                    {
                        "entity_id": f"source:{name}",
                        "evidence_id": f"evidence-{name}",
                        "field": "evidence",
                        "quote": "source-backed",
                    }
                ],
            }
        )
        cases.append(
            {
                "id": "negative-" + name,
                "split": split,
                "source": "source",
                "query": "missing " + name,
                "task": "no_match",
                "entity_group": "negative-" + name,
                "negative_domain": "in_domain",
                "expected_ids": [],
                "confusable_ids": [],
                "evidence": [],
            }
        )
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "frozen_before_retrieval",
                "source_content_sha256": "same-source",
                "source_entities_sha256": manifest["files"]["index.json"],
                "cases": cases,
                "limitations": ["Synthetic fixture."],
            }
        )
    )
    baseline, candidate = tmp_path / "baseline", tmp_path / "candidate"
    baseline.write_bytes(b"baseline executable")
    candidate.write_bytes(b"candidate executable")
    return baseline, candidate, before, after, fixture


def result(name: str) -> dict:
    return {
        "id": "source:" + name,
        "source": "source",
        "score": 0.5,
        "cosine": 0.5,
        "evidence_id": "evidence-" + name,
        "matches": [
            {
                "channel": "text",
                "score": 0.5,
                "evidence_id": "evidence-" + name,
                "url": f"https://example.test/{name}",
            }
        ],
    }


def runner(binary: Path, *args: str) -> dict:
    if args == ("info",):
        return {"dataset_id": binary.name}
    name = "two" if args[-1].endswith("two") else "one"
    items = [result(name)]
    if binary.name == "baseline":
        items.insert(0, result("other"))
    return {
        "query_type": "text",
        "match_status": "no_supported_match"
        if args[-1].startswith("missing") and binary.name == "candidate"
        else "candidates",
        "results": items,
    }


def test_source_backed_fixture_is_frozen_before_retrieval() -> None:
    path = Path(__file__).parent / "fixtures/ranking.json"
    assert (
        hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        == "4924a73a2cee427d37ab7e6ccfa8a776735ccd40305844ebbb071ad526e7b979"
    )
    fixture = json.loads(path.read_bytes())
    assert len(fixture["cases"]) == 44
    assert len({case["source"] for case in fixture["cases"]}) == 11
    for split in ("development", "evaluation"):
        rows = [case for case in fixture["cases"] if case["split"] == split]
        assert sum(bool(case["expected_ids"]) for case in rows) == 18
        assert sum(case.get("negative_domain") == "in_domain" for case in rows) == 2
        assert sum(case.get("negative_domain") == "unrelated" for case in rows) == 2
    groups = [
        {case["entity_group"] for case in fixture["cases"] if case["split"] == split}
        for split in ("development", "evaluation")
    ]
    assert not groups[0] & groups[1]


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate",
        "cross_split",
        "group",
        "quote",
        "evidence",
        "corpus",
        "source",
        "negative",
        "overlap",
    ],
)
def test_fixture_rejects_unjustified_labels_or_split_leakage(
    tmp_path: Path, failure: str
) -> None:
    _, _, bundle, _, path = setup_benchmark(tmp_path)
    fixture = json.loads(path.read_bytes())
    if failure == "duplicate":
        fixture["cases"][1]["id"] = fixture["cases"][0]["id"]
    elif failure == "cross_split":
        fixture["cases"][2]["expected_ids"] = ["source:one"]
    elif failure == "group":
        fixture["cases"][2]["entity_group"] = "one"
    elif failure == "quote":
        fixture["cases"][0]["evidence"][0]["quote"] = "Invented source text"
    elif failure == "evidence":
        fixture["cases"][0]["evidence"] = []
    elif failure == "corpus":
        fixture["source_content_sha256"] = "other-corpus"
    elif failure == "source":
        fixture["cases"][0]["source"] = "absent"
    elif failure == "negative":
        fixture["cases"][1]["negative_domain"] = "unknown"
    elif failure == "overlap":
        fixture["cases"][0]["confusable_ids"] = ["source:one"]
    with zipfile.ZipFile(bundle) as archive, pytest.raises(ValueError):
        validate_fixture(fixture, archive, json.loads(archive.read("manifest.json")))


@pytest.mark.parametrize(
    "failure",
    [
        "duplicate",
        "filter",
        "nonfinite",
        "evidence",
        "url",
        "missing_matches",
        "generated",
        "envelope",
    ],
)
def test_invalid_search_responses_cannot_be_scored(
    tmp_path: Path, failure: str
) -> None:
    baseline, _, bundle, _, path = setup_benchmark(tmp_path)
    response = runner(baseline, "search", "one")
    if failure == "duplicate":
        response["results"].append(deepcopy(response["results"][0]))
    elif failure == "filter":
        response["results"][0]["source"] = "other"
    elif failure == "nonfinite":
        response["results"][0]["score"] = float("nan")
    elif failure == "evidence":
        response["results"][0]["evidence_id"] = "evidence-two"
    elif failure == "url":
        response["results"][0]["matches"][0]["url"] = "https://unrelated.test/"
    elif failure == "missing_matches":
        response["results"][0]["matches"] = []
    elif failure == "generated":
        response["results"][0]["matches"][0]["channel"] = "description"
    elif failure == "envelope":
        response["match_status"] = "confident"
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entities = validate_fixture(json.loads(path.read_bytes()), archive, manifest)
        with pytest.raises(ValueError):
            validate_response(response, entities, archive, manifest, "source")


def test_ranked_results_and_abstentions_are_separate() -> None:
    rows = [
        {
            "expected_ids": ["source:one"],
            "confusable_ids": ["source:other"],
            "entity_group": "one",
            "baseline": {
                "rank": 2,
                "first": "source:other",
                "match_status": "candidates",
            },
            "candidate": {
                "rank": 1,
                "first": "source:one",
                "match_status": "no_supported_match",
            },
        }
    ]
    summary = summarize(rows)
    assert summary["improved_ranks"] == 1
    assert summary["candidate"]["ranked_top1"]["rate"] == 1
    assert summary["candidate"]["accepted_top1"]["rate"] == 0
    assert summary["candidate"]["accepted_recall_at_5"]["rate"] == 0
    assert summary["baseline"]["wrong_variant_top1"]["rate"] == 1
    assert summary["candidate"]["ranked_top1"]["wilson95"][0] < 1
    assert summary["candidate"]["false_acceptance"]["rate"] is None


def test_identity_snippet_retains_empty_evidence_with_resolved_contributions(
    tmp_path: Path,
) -> None:
    baseline, _, bundle, _, path = setup_benchmark(tmp_path)
    response = runner(baseline, "search", "one")
    response["results"][0]["evidence_id"] = ""
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        entities = validate_fixture(json.loads(path.read_bytes()), archive, manifest)
        validate_response(response, entities, archive, manifest, "source")
        response["results"][0]["matches"][0]["evidence_id"] = ""
        with pytest.raises(ValueError, match="evidence contribution"):
            validate_response(response, entities, archive, manifest, "source")


def test_comparison_freezes_artifacts_and_preserves_global_and_filtered_slices(
    tmp_path: Path,
) -> None:
    args = setup_benchmark(tmp_path)
    calls = []

    def run(binary: Path, *command: str) -> dict:
        calls.append((binary, command))
        return runner(binary, *command)

    with pytest.raises(ValueError, match="frozen development selection"):
        benchmark(*args, "evaluation", runner=run)
    assert not calls
    report = benchmark(*args, "development", mode="vector", runner=run)
    assert len(report["cases"]) == 4
    assert set(report["metrics"]) == {"global", "source_filtered"}
    assert report["by_source"]["source"]["global"]["improved_ranks"] == 1
    assert report["metrics"]["global"]["candidate"]["false_acceptance"]["rate"] == 0
    for binary, command in calls:
        if command[0] == "search":
            assert command[2] == ("vector" if binary == args[1] else "hybrid")
    report_path, selection = tmp_path / "development.json", tmp_path / "selection.json"
    report_path.write_text(json.dumps(report))
    freeze_selection(report_path, selection)
    with pytest.raises(ValueError, match="cannot be overwritten"):
        freeze_selection(report_path, selection)
    evaluation = benchmark(
        *args, "evaluation", mode="vector", selection=selection, runner=run
    )
    assert all(row["id"] in {"two", "negative-two"} for row in evaluation["cases"])
    assert evaluation["release_quality_established"] is False
    report_path.write_text(json.dumps(evaluation))
    with pytest.raises(ValueError, match="Only development"):
        freeze_selection(report_path, tmp_path / "invalid.json")


@pytest.mark.parametrize("failure", ["binary", "bundle", "mode", "report", "selection"])
def test_changed_selection_inputs_reject_evaluation_before_running(
    tmp_path: Path, failure: str
) -> None:
    args = setup_benchmark(tmp_path)
    report = benchmark(*args, "development", runner=runner)
    report_path, selection = tmp_path / "development.json", tmp_path / "selection.json"
    report_path.write_text(json.dumps(report))
    freeze_selection(report_path, selection)
    mode = "hybrid"
    if failure == "binary":
        args[1].write_bytes(b"different candidate")
    elif failure == "bundle":
        with zipfile.ZipFile(args[3], "a") as archive:
            archive.comment = b"changed artifact"
    elif failure == "mode":
        mode = "vector"
    elif failure == "report":
        report_path.write_bytes(report_path.read_bytes() + b" ")
    elif failure == "selection":
        frozen = json.loads(selection.read_bytes())
        frozen["selection_split"] = "evaluation"
        selection.write_text(json.dumps(frozen))

    def never_run(*unused: object) -> dict:
        pytest.fail("Mismatched frozen artifacts must fail before executing queries")

    with pytest.raises(ValueError, match="Frozen development selection"):
        benchmark(*args, "evaluation", mode=mode, selection=selection, runner=never_run)
