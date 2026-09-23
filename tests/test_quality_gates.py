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
    calibration_proof,
    calibration_separation,
    evaluate,
    metrics,
    passing,
    query_failure,
    replay_research,
    response_integrity,
    text_regression,
    validate_report,
)


def test_recorded_image_rejections_require_bound_queries_and_exact_cli_errors() -> None:
    case = {"query": {"image_id": "query", "text": "radar"}}
    picture = {"sha256": "a" * 64}
    row = {
        "failure": {
            "exit_code": 1,
            "stdout": "",
            "stderr": '{"error":"invalid or truncated image"}\n',
            "dataset_id": "dataset",
            "query": "radar",
            "query_image_sha256": "a" * 64,
        }
    }
    query_failure(row, case, picture, "dataset")
    row["response"] = {"results": []}
    with pytest.raises(ValueError, match="Invalid captured"):
        query_failure(row, case, picture, "dataset")
    row.pop("response")
    row["failure"]["query_image_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="Invalid captured"):
        query_failure(row, case, picture, "dataset")
    row["failure"]["query_image_sha256"] = "a" * 64
    row["failure"]["stderr"] = '{"error":"unrelated inference failure"}'
    with pytest.raises(ValueError, match="Invalid captured"):
        query_failure(row, case, picture, "dataset")


def calibration_inputs(
    tmp_path: Path,
    *,
    review_quote: str = "Archived source identity for test:one",
    query_type: str = "text",
) -> tuple[dict, dict, dict]:
    from pipelines.calibration import (
        canonical,
        decide,
        digest,
        fit,
        retrieval_identity,
        scope_identity,
    )

    ids = ["test:one", "test:three", "test:two"]
    eligible = ids.copy()
    image_only = query_type == "image"
    if image_only:
        ids.append("test:unindexed")
    values: dict = {
        "index.json": [{"id": key, "source": "test"} for key in ids],
        "image/index.json": [],
        "observations/index.json": [],
        "observations/recipes.json": {},
    }
    if image_only:
        values["image/index.json"] = [
            {
                "id": key,
                "vector_index": position if key in eligible else None,
                "references": [{"entity_id": key, "evidence_id": "page"}],
            }
            for position, key in enumerate(ids)
        ]
    for key in ids:
        values["entities/" + key.replace(":", "/") + ".json"] = {
            "id": key,
            "evidence": [
                {
                    "id": "page",
                    "url": "https://example.test/" + key,
                    "markdown": "Archived source identity for " + key,
                }
            ],
        }
    members = {name: canonical(value) for name, value in values.items()}
    base: dict = {
        "format_version": 4,
        "dataset_id": "base-dataset",
        "files": {name: digest(raw) for name, raw in members.items()},
        "image": {"search": {"calibration": None}, "model_sha256": "a" * 64},
        "observations": {},
    }
    binding = retrieval_identity(base)
    executable = tmp_path / "development-cli"
    executable.write_bytes(b"synthetic development executable identity")
    contract = quality_gates.ROOT / "tests/fixtures/search_acceptance.json"
    cases = []
    captures = []
    pictures = []

    def result(key: str, score: float) -> dict:
        return {
            "id": key,
            "source": "test",
            "score": score,
            "cosine": None if image_only else score,
            "name_match": False,
            "evidence_id": "page",
            "matches": [
                {
                    "channel": "image" if image_only else "text",
                    "score": score,
                    "evidence_id": "page",
                    "url": "https://example.test/" + key,
                    **(
                        {"media_id": key, "model_sha256": "a" * 64}
                        if image_only
                        else {}
                    ),
                }
            ],
        }

    for number in range(41):
        expected = ["test:one"] if number == 0 else []
        query = "Synthetic development input " + str(number)
        image_sha256 = digest(query.encode()) if image_only else None
        if image_only:
            pictures.append(
                {
                    "id": image_sha256,
                    "sha256": image_sha256,
                    "status": "captured",
                    "split": "development",
                    "photo_group": "development-" + str(number),
                }
            )
            query = ""
        review = {
            "reviewer": "synthetic unit test",
            "rationale": "Synthetic fixture validates proof mechanics only",
            "evidence": [
                {
                    "entity_id": "test:one",
                    "evidence_id": "page",
                    "quote": review_quote,
                }
            ]
            if expected
            else [],
        }
        cases.append(
            {
                "id": str(number),
                "split": "development",
                "reviewed": True,
                "label_basis": "Synthetic source-grounded test",
                "group_id": "development-" + str(number),
                "scope": "global",
                "query_type": query_type,
                "text_mode": None if image_only else "vector",
                "observations": False,
                "query": {"text": query, "image_sha256": image_sha256},
                "expected_ids": expected,
                "confusable_ids": [],
                "review": review,
                "negative_domain": "unrelated",
            }
        )
        response = {
            "dataset_id": base["dataset_id"],
            "query_type": query_type,
            "mode": "image" if image_only else "vector",
            "query": query,
            "query_image_sha256": image_sha256,
            "match_status": "no_supported_match" if image_only else "candidates",
            "calibration_status": "uncalibrated",
            "results": [
                result("test:one", 0.8 if expected else 0.1),
                result("test:two", 0.0),
            ],
        }
        captures.append(
            {
                "case_id": str(number),
                "eligible_entities_sha256": scope_identity(eligible),
                "response": response,
            }
        )
    development = {
        "schema_version": 1,
        "status": "reviewed_frozen_development",
        "retrieval_sha256": binding,
        "scopes": {"global": eligible},
        "cases": cases,
    }
    fixture_bytes = canonical(development)
    responses = {
        "schema_version": 1,
        "protocol": "calibration-development-responses-v1",
        "split": "development",
        "fixture_sha256": digest(fixture_bytes),
        "binary_sha256": digest(executable.read_bytes()),
        "dataset_id": base["dataset_id"],
        "retrieval_sha256": binding,
        "cases": captures,
    }
    responses_bytes = canonical(responses)
    fitted = fit(
        base,
        fixture_bytes,
        responses_bytes,
        binary_sha256=digest(executable.read_bytes()),
        contract_bytes=contract.read_bytes(),
    )
    body = canonical(fitted)
    final = {
        **base,
        "format_version": 5,
        "dataset_id": "final-dataset",
        "files": {**base["files"], "calibration.json": digest(body)},
        "calibration": {
            "schema_version": 1,
            "member": "calibration.json",
            "sha256": digest(body),
            "profiles": len(fitted["profiles"]),
            "retrieval_sha256": binding,
        },
    }
    paths = {"development_binary": executable, "contract": contract}
    for name, manifest, content in (
        ("development_bundle", base, members),
        ("bundle", final, {**members, "calibration.json": body}),
    ):
        path = tmp_path / (name + ".zip")
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("manifest.json", canonical(manifest))
            for member, raw in content.items():
                archive.writestr(member, raw)
        paths[name] = path
    for name, raw in (
        ("development_fixture", fixture_bytes),
        ("development_responses", responses_bytes),
    ):
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_bytes(raw)
    fixture: dict = {
        "status": "frozen_before_retrieval",
        "calibration": {
            "artifact_sha256": digest(body),
            "development_fixture_sha256": digest(fixture_bytes),
            "development_responses_sha256": digest(responses_bytes),
        },
        "media": pictures,
        "cases": [
            {
                "id": "held-out",
                "split": "evaluation",
                "status": "ready",
                "query": {"text": "Separate held-out query", "source": "test"},
                "expected_ids": ["test:three"],
                "confusable_ids": [],
            }
        ],
    }
    if image_only:
        pictures.append(
            {
                "id": "held-out-image",
                "sha256": "b" * 64,
                "status": "captured",
                "split": "evaluation",
                "photo_group": "held-out",
            }
        )
        fixture["cases"][0]["query"] = {"image_id": "held-out-image", "source": "test"}
    paths["fixture"] = tmp_path / "evaluation-fixture.json"
    paths["fixture"].write_bytes(canonical(fixture))
    response = {
        "dataset_id": final["dataset_id"],
        "query_type": query_type,
        "mode": "image" if image_only else "vector",
        "query": "" if image_only else "Separate held-out query",
        "query_image_sha256": "b" * 64 if image_only else None,
        "results": [result("test:three", 0.9), result("test:one", 0.0)],
    }
    context = {
        "query_type": query_type,
        "text_mode": None if image_only else "vector",
        "observations": False,
        "eligible_entities_sha256": scope_identity(eligible),
    }
    response.update(decide(fitted, context, response) or {})
    evaluation = {
        "fixture_sha256": digest(paths["fixture"].read_bytes()),
        "calibration_sha256": digest(body),
        "cases": [
            {
                "id": "held-out",
                "scope": "global",
                "eligible_entities_sha256": scope_identity(eligible),
                "response": response,
            }
        ],
    }
    return paths, fixture, evaluation


def test_calibration_proof_refits_and_replays_captured_decisions(
    tmp_path: Path,
) -> None:
    paths, fixture, evaluation = calibration_inputs(tmp_path)
    report = calibration_proof(paths, fixture, evaluation)
    assert report["development_cases"] == 41
    assert report["held_out_decisions"] == 1
    assert len(report["frozen_seed_sha256"]) == 4


@pytest.mark.parametrize("scope", ["global", "source_filtered"])
def test_image_calibration_proof_excludes_unindexed_entities(
    tmp_path: Path,
    scope: str,
) -> None:
    paths, fixture, evaluation = calibration_inputs(tmp_path, query_type="image")
    evaluation["cases"][0]["scope"] = scope
    assert response_integrity(evaluation, fixture, paths["bundle"]) == 2
    assert calibration_proof(paths, fixture, evaluation)["development_photos"] == 41
    from pipelines.calibration import scope_identity

    evaluation["cases"][0]["eligible_entities_sha256"] = scope_identity(
        [
            "test:one",
            "test:two",
            "test:three",
            "test:unindexed",
        ]
    )
    with pytest.raises(ValueError, match="eligible pool binding"):
        calibration_proof(paths, fixture, evaluation)


def test_calibration_proof_rejects_review_claim_without_archived_quote(
    tmp_path: Path,
) -> None:
    paths, fixture, evaluation = calibration_inputs(
        tmp_path, review_quote="An invented source claim"
    )
    with pytest.raises(ValueError, match="archived evidence"):
        calibration_proof(paths, fixture, evaluation)


def test_calibration_proof_rejects_rehashed_threshold_change(tmp_path: Path) -> None:
    from pipelines.calibration import canonical, digest

    paths, fixture, evaluation = calibration_inputs(tmp_path)
    with zipfile.ZipFile(paths["bundle"]) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    fitted = json.loads(members["calibration.json"])
    fitted["profiles"][0]["minimums"]["semantic_cosine"] -= 1
    members["calibration.json"] = canonical(fitted)
    checksum = digest(members["calibration.json"])
    manifest["files"]["calibration.json"] = checksum
    manifest["calibration"]["sha256"] = checksum
    members["manifest.json"] = canonical(manifest)
    with zipfile.ZipFile(paths["bundle"], "w") as archive:
        for name, raw in members.items():
            archive.writestr(name, raw)
    with pytest.raises(ValueError, match="does not reproduce"):
        calibration_proof(paths, fixture, evaluation)


@pytest.mark.parametrize(
    "damage", ["decision", "scope", "selection", "executable", "response", "missing"]
)
def test_calibration_proof_rejects_changed_or_missing_evidence(
    tmp_path: Path, damage: str
) -> None:
    paths, fixture, evaluation = calibration_inputs(tmp_path)
    if damage == "decision":
        evaluation["cases"][0]["response"]["decision"]["features"][
            "semantic_cosine"
        ] += 1
    elif damage == "scope":
        evaluation["cases"][0]["eligible_entities_sha256"] = "0" * 64
    elif damage == "selection":
        fixture["calibration"]["artifact_sha256"] = "0" * 64
    elif damage == "executable":
        paths["development_binary"].write_bytes(b"changed executable")
    elif damage == "response":
        captured = json.loads(paths["development_responses"].read_bytes())
        captured["cases"][0]["response"]["results"][0]["cosine"] = 0.7
        paths["development_responses"].write_text(
            json.dumps(captured), encoding="utf-8"
        )
    else:
        paths.pop("development_responses")
    with pytest.raises(ValueError):
        calibration_proof(paths, fixture, evaluation)


@pytest.mark.parametrize(
    "damage", ["photo_group", "photo_bytes", "text_entity", "text_query", "frozen_seed"]
)
def test_calibration_separation_rejects_held_out_and_seed_reuse(damage: str) -> None:
    development: dict = {
        "cases": [
            {
                "group_id": "dev-photo",
                "query": {"text": "independent query", "image_sha256": "a" * 64},
                "expected_ids": ["test:one"],
                "confusable_ids": [],
            }
        ]
    }
    fixture = {
        "media": [
            {
                "status": "captured",
                "split": "development",
                "sha256": "a" * 64,
                "photo_group": "dev-photo",
            },
            {
                "status": "captured",
                "split": "evaluation",
                "sha256": "b" * 64,
                "photo_group": "eval-photo",
            },
        ],
        "cases": [
            {
                "split": "evaluation",
                "query": {"text": "held-out phrase"},
                "expected_ids": ["test:held"],
                "confusable_ids": [],
            }
        ],
    }
    assert calibration_separation(development, fixture)["development_photos"] == 1
    case = development["cases"][0]
    if damage == "photo_group":
        case["group_id"] = "eval-photo"
    elif damage == "photo_bytes":
        case["query"]["image_sha256"] = "b" * 64
    elif damage == "text_entity":
        case["query"]["image_sha256"] = None
        case["expected_ids"] = ["test:held"]
    elif damage == "text_query":
        case["query"]["image_sha256"] = None
        case["query"]["text"] = "  HELD-out phrase  "
    else:
        seed = json.loads(
            (quality_gates.ROOT / "tests/fixtures/retrieval.json").read_bytes()
        )
        case["query"]["image_sha256"] = None
        case["expected_ids"] = [seed[0]["expected"]]
    with pytest.raises(ValueError):
        calibration_separation(development, fixture)


def test_combined_calibration_separates_photos_not_generic_constraint_text() -> None:
    development: dict = {
        "cases": [
            {
                "group_id": "development-photo",
                "query": {"text": "rifle", "image_sha256": "a" * 64},
                "expected_ids": [],
                "confusable_ids": [],
            }
        ]
    }
    fixture = {
        "media": [
            {
                "status": "captured",
                "split": split,
                "sha256": checksum * 64,
                "photo_group": split + "-photo",
            }
            for split, checksum in [("development", "a"), ("evaluation", "b")]
        ],
        "cases": [
            {
                "split": "evaluation",
                "group_id": "evaluation-photo",
                "query": {"text": "rifle", "image_id": "held-image"},
                "expected_ids": [],
                "confusable_ids": [],
            }
        ],
    }
    assert calibration_separation(development, fixture)["development_photos"] == 1
    development["cases"][0]["query"]["image_sha256"] = "b" * 64
    with pytest.raises(ValueError, match="separate registered development photo group"):
        calibration_separation(development, fixture)


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


@pytest.mark.parametrize(
    "damage", [None, "evidence", "media", "duplicate", "missing_matches"]
)
@pytest.mark.parametrize("metadata_snippet", [False, True])
def test_response_integrity_checks_actual_source_references(
    tmp_path: Path, damage: str | None, metadata_snippet: bool
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
        "evidence_id": "" if metadata_snippet else "page",
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
    elif damage == "missing_matches":
        result["matches"] = []
    fixture: dict = {
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
        picture = {"id": "photo", "sha256": "a" * 64}
        fixture["media"] = [picture]
        fixture["cases"][0]["query"]["image_id"] = "photo"
        evaluation["cases"][0].pop("response")
        evaluation["cases"][0]["failure"] = {
            "exit_code": 1,
            "stdout": "",
            "stderr": '{"error":"invalid or truncated image"}',
            "dataset_id": "dataset",
            "query": "query",
            "query_image_sha256": picture["sha256"],
        }
        with pytest.raises(ValueError, match="rejected query"):
            response_integrity(evaluation, fixture, bundle)
        assert (
            response_integrity(evaluation, fixture, bundle, allow_image_rejections=True)
            == 0
        )
