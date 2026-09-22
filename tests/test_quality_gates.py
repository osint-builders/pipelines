import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
import quality_gates
from quality_gates import (
    REQUIRED_RELEASE_CHECKS,
    artifact,
    evaluate,
    metrics,
    passing,
    replay_research,
    response_integrity,
    text_regression,
    validate_report,
)


def test_abstained_suggestions_count_as_positive_misses() -> None:
    row = {
        "expected_ids": ["source:entity"],
        "confusable_ids": ["source:variant"],
        "photo_group": "photo",
        "accepted": False,
        "rank": 1,
        "first": "source:entity",
    }
    report = metrics([row, {**row, "accepted": True, "rank": 2}])
    assert report["top1"]["numerator"] == 0
    assert report["recall_at_5"]["numerator"] == 1
    assert report["recall_at_5"]["denominator"] == 2
    assert report["positive_photo_groups"] == 1


def test_zero_false_acceptance_with_one_negative_fails_wilson_target() -> None:
    report = metrics([{"expected_ids": [], "accepted": False}])
    assert not passing(
        report,
        {
            "false_acceptance_max": 0.05,
            "false_acceptance_wilson95_upper_max": 0.1,
        },
    )
    assert not passing(metrics([]), {"top1_min": 0.8})


def artifacts(tmp_path: Path) -> dict:
    binary = tmp_path / "cli"
    binary.write_bytes(b"test binary identity")
    index = json.dumps([{"id": "source:entity", "source": "source"}]).encode()
    bundle = tmp_path / "dataset.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("index.json", index)
        archive.writestr(
            "manifest.json",
            json.dumps(
                {
                    "dataset_id": "test-dataset",
                    "content_sha256": "test-content",
                    "files": {"index.json": hashlib.sha256(index).hexdigest()},
                }
            ),
        )
    return {
        "binary": artifact(binary),
        "bundle": artifact(bundle),
        "contract": artifact(
            quality_gates.ROOT / "tests/fixtures/search_acceptance.json"
        ),
    }


def test_missing_evidence_fails_every_named_release_category(tmp_path: Path) -> None:
    report = evaluate(artifacts(tmp_path))
    assert tuple(row["name"] for row in report["checks"]) == REQUIRED_RELEASE_CHECKS
    assert all(row["passed"] is False for row in report["checks"])
    assert report["release_quality_established"] is False
    with pytest.raises(ValueError, match="not satisfied"):
        validate_report(report)


def test_changed_artifact_and_asserted_passes_are_rejected(tmp_path: Path) -> None:
    evidence = artifacts(tmp_path)
    report = evaluate(evidence)
    report["checks"][0]["passed"] = True
    report["release_quality_established"] = True
    with pytest.raises(ValueError, match="recomputed evidence"):
        validate_report(report)
    Path(evidence["binary"]["path"]).write_bytes(b"different executable")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluate(evidence)


def test_portable_paths_keep_original_artifact_hashes(tmp_path: Path) -> None:
    evidence = artifacts(tmp_path)
    report = evaluate(evidence)
    replacement = tmp_path / "relocated-cli"
    replacement.write_bytes(Path(evidence["binary"]["path"]).read_bytes())
    with pytest.raises(ValueError, match="not satisfied"):
        validate_report(report, {"binary": replacement})
    replacement.write_bytes(b"incorrect")
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_report(report, {"binary": replacement})


def test_missing_or_duplicate_ready_evaluation_cases_do_not_pass(
    tmp_path: Path,
) -> None:
    evidence = artifacts(tmp_path)
    fixture = tmp_path / "fixture.json"
    fixture.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "status": "frozen_before_retrieval",
                "source_content_sha256": "test-content",
                "media": [],
                "cases": [
                    {
                        "id": "case",
                        "split": "evaluation",
                        "status": "ready",
                        "task": "exact_designation",
                        "query": {"text": "entity", "source": "source"},
                        "expected_ids": ["source:entity"],
                        "confusable_ids": [],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    evaluation = tmp_path / "evaluation.json"
    evaluation.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "split": "evaluation",
                "fixture_sha256": quality_gates.sha256(fixture),
                "binary_sha256": evidence["binary"]["sha256"],
                "dataset_id": "test-dataset",
                "gallery_sha256": None,
                "cases": [],
            }
        ),
        encoding="utf-8",
    )
    evidence.update(fixture=artifact(fixture), evaluation=artifact(evaluation))
    report = evaluate(evidence)
    checks = {row["name"]: row for row in report["checks"]}
    assert checks["frozen_evaluation_identity"]["passed"] is False
    assert "every ready case" in checks["frozen_evaluation_identity"]["detail"]
    assert checks["exact_designation"]["detail"]["top1"]["denominator"] == 0


def test_regression_recomputes_bounds_and_rejects_missing_sources() -> None:
    fixture = [
        {
            "source": "source",
            "query": "name",
            "expected": "source:entity",
            "max_rank": 1,
        }
    ]
    identity = {
        "binary_sha256": "binary",
        "fixture_sha256": "fixture",
        "dataset_id": "new",
        "content_sha256": "content",
        "baseline_dataset_id": "old",
    }
    current: dict = {
        "binary": {"sha256": "binary"},
        "cases": {"sha256": "fixture"},
        "dataset": {"content_sha256": "content"},
        "evaluation": {
            "dataset_id": "new",
            "results": [{**fixture[0], "rank": 1, "passed": False}],
            "skipped": [],
        },
    }
    baseline = {**current, "evaluation": {**current["evaluation"], "dataset_id": "old"}}
    assert text_regression(current, baseline, fixture, identity)[0]
    current["evaluation"]["results"] = [{**fixture[0], "rank": 2, "passed": True}]
    assert not text_regression(current, baseline, fixture, identity)[0]
    current["evaluation"]["results"] = []
    current["evaluation"]["skipped"] = [{"source": "source"}]
    assert not text_regression(current, baseline, fixture, identity)[0]


@pytest.mark.parametrize("change", ["query_text", "query_image", "duplicate_query"])
def test_swapped_responses_and_duplicate_queries_fail(
    tmp_path: Path, change: str
) -> None:
    evidence = artifacts(tmp_path)
    query_file = tmp_path / "query.png"
    query_file.write_bytes(b"frozen query bytes")
    picture = {
        "id": "picture",
        "split": "evaluation",
        "status": "captured",
        "photo_group": "photo",
        "sha256": quality_gates.sha256(query_file),
        "local_path": str(query_file),
    }
    case = {
        "id": "case",
        "split": "evaluation",
        "status": "ready",
        "task": "photograph",
        "query": {"image_id": "picture", "text": "entity", "source": "source"},
        "expected_ids": ["source:entity"],
        "confusable_ids": [],
    }
    fixture: dict = {
        "schema_version": 1,
        "status": "frozen_before_retrieval",
        "source_content_sha256": "test-content",
        "media": [picture],
        "cases": [case],
    }
    response = {
        "dataset_id": "test-dataset",
        "query_type": "image_text",
        "query": "entity",
        "query_image_sha256": picture["sha256"],
        "match_status": "candidates",
        "results": [{"id": "source:entity"}],
    }
    if change == "query_text":
        response["query"] = "different query"
    elif change == "query_image":
        response["query_image_sha256"] = "different image"
    else:
        fixture["cases"].append({**case, "id": "duplicate"})
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    evaluation = {
        "schema_version": 1,
        "split": "evaluation",
        "fixture_sha256": quality_gates.sha256(fixture_path),
        "binary_sha256": evidence["binary"]["sha256"],
        "dataset_id": "test-dataset",
        "gallery_sha256": None,
        "cases": [
            {"id": "case", "scope": scope, "response": response}
            for scope in ("global", "source_filtered")
        ],
    }
    evaluation_path = tmp_path / "evaluation.json"
    evaluation_path.write_text(json.dumps(evaluation), encoding="utf-8")
    evidence.update(
        fixture=artifact(fixture_path), evaluation=artifact(evaluation_path)
    )
    report = evaluate(evidence)
    check = next(
        row for row in report["checks"] if row["name"] == "frozen_evaluation_identity"
    )
    assert check["passed"] is False
    assert (
        "Duplicate query" if change == "duplicate_query" else "query bytes/text"
    ) in check["detail"]


def test_negative_derivatives_count_one_independent_photo_group() -> None:
    row = {
        "expected_ids": [],
        "accepted": False,
        "photo_group": "one-photo",
        "negative_domain": "in_domain",
    }
    report = metrics([dict(row) for _ in range(100)])
    assert report["negative_cases"] == 100
    assert report["negative_photo_groups"] == 1


def test_research_proof_replays_payloads_and_rejects_changed_claim(
    tmp_path: Path,
) -> None:
    from accept_cli import accept_research
    from test_research_cli_acceptance import research_acceptance_fixture

    body, manifest, info, responses = research_acceptance_fixture()
    binary = tmp_path / "cli"
    binary.write_bytes(b"binary")
    bundle = tmp_path / "bundle.zip"
    with (
        zipfile.ZipFile(bundle, "w") as archive,
        zipfile.ZipFile(io.BytesIO(body)) as source,
    ):
        for name in source.namelist():
            archive.writestr(name, source.read(name))
        archive.writestr("manifest.json", json.dumps(manifest))
    commands: list[dict] = [{"args": ["info"], "response": info}]

    def run(*args: str) -> bytes:
        response = {"dataset_id": manifest["dataset_id"], **responses[args]}
        commands.append({"args": list(args), "response": response})
        return json.dumps(response).encode()

    with zipfile.ZipFile(bundle) as archive:
        accept_research(run, archive, manifest, info)
    proof = {
        "schema_version": 1,
        "protocol_sha256": quality_gates.sha256(
            quality_gates.ROOT / "tools/accept_cli.py"
        ),
        "binary_sha256": quality_gates.sha256(binary),
        "bundle_sha256": quality_gates.sha256(bundle),
        "dataset_id": manifest["dataset_id"],
        "commands": commands,
    }
    assert replay_research(proof, binary, bundle) == len(commands)
    claim = next(
        command for command in commands if command["args"] == ["facts", "sample:one"]
    )
    claim["response"]["claims"][0]["raw"] = {"fabricated": "statement"}
    with pytest.raises(AssertionError):
        replay_research(proof, binary, bundle)


@pytest.mark.parametrize("damage", [None, "evidence", "media", "duplicate"])
def test_response_integrity_checks_actual_source_references(
    tmp_path: Path, damage: str | None
) -> None:
    bundle = tmp_path / "bundle.zip"
    values = {
        "index.json": [{"id": "source:entity", "source": "source"}],
        "entities/source/entity.json": {
            "evidence": [{"id": "page", "url": "https://example.com/entity"}]
        },
    }
    members = {name: json.dumps(value).encode() for name, value in values.items()}
    manifest = {
        "dataset_id": "dataset",
        "files": {
            name: hashlib.sha256(raw).hexdigest() for name, raw in members.items()
        },
    }
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
        archive.writestr("manifest.json", json.dumps(manifest))
    match = {
        "channel": "text",
        "score": 0.5,
        "evidence_id": "page",
        "url": "https://example.com/entity",
    }
    result = {
        "id": "source:entity",
        "source": "source",
        "score": 0.5,
        "cosine": 0.5,
        "evidence_id": "page",
        "matches": [match],
    }
    response: dict = {
        "dataset_id": "dataset",
        "query_type": "text",
        "match_status": "candidates",
        "results": [result],
    }
    if damage == "evidence":
        match["evidence_id"] = "unrelated"
    elif damage == "media":
        match["media_id"] = "unrelated-media"
    elif damage == "duplicate":
        response["results"].append(result)
    fixture = {
        "cases": [{"id": "case", "query": {"text": "query", "source": "source"}}],
        "media": [],
    }
    evaluation = {
        "cases": [{"id": "case", "scope": "source_filtered", "response": response}]
    }
    if damage:
        with pytest.raises(ValueError):
            response_integrity(evaluation, fixture, bundle)
    else:
        assert response_integrity(evaluation, fixture, bundle) == 1
