import json
from copy import deepcopy
from pathlib import Path

import pytest

from pipelines import calibration as c

FIXTURE = json.loads((Path(__file__).parent / "fixtures/calibration.json").read_bytes())
CONTRACT = (Path(__file__).parent / "fixtures/search_acceptance.json").read_bytes()
BINARY = "6" * 64


def development(modes: list[dict] | None = None) -> tuple[dict, dict, dict]:
    manifest = deepcopy(FIXTURE["binding"]["manifest"])
    binding = c.retrieval_identity(manifest)
    eligible = c.scope_identity(["test:one", "test:two"])
    fixture: dict = {
        "schema_version": 1,
        "status": "reviewed_frozen_development",
        "retrieval_sha256": binding,
        "scopes": {"global": ["test:one", "test:two"]},
        "cases": [],
    }
    captures = []
    for mode_index, mode in enumerate(modes or [c.PROTOCOL["profiles"][0]]):
        for index in range(102):
            positive = index < 2
            case_id = f"mode-{mode_index}-case-{index}"
            text = "" if mode["query_type"] == "image" else case_id
            picture = (
                None if mode["query_type"] == "text" else c.digest(case_id.encode())
            )
            fixture["cases"].append(
                {
                    "id": case_id,
                    "split": "development",
                    "reviewed": True,
                    "label_basis": "Synthetic test label",
                    "group_id": case_id,
                    "scope": "global",
                    **mode,
                    "query": {"text": text, "image_sha256": picture},
                    "expected_ids": ["test:one"] if positive else [],
                    "confusable_ids": ["test:two"] if positive else [],
                }
            )
            support, margin = (0.8, 0.4) if positive else (0.2, 0.1)
            response = {
                "dataset_id": manifest["dataset_id"],
                "query": text,
                "query_type": mode["query_type"],
                "mode": mode["text_mode"]
                if mode["query_type"] == "text"
                else mode["query_type"],
                "observations": mode["observations"],
                "results": [
                    {
                        "id": "test:one",
                        "score": 0.5 + margin,
                        "cosine": support if mode["query_type"] != "image" else None,
                        "matches": [{"channel": "image", "score": support}]
                        if mode["query_type"] != "text"
                        else [],
                    },
                    {"id": "test:two", "score": 0.5, "cosine": 0.1, "matches": []},
                ],
            }
            if picture:
                response["query_image_sha256"] = picture
            captures.append(
                {
                    "case_id": case_id,
                    "eligible_entities_sha256": eligible,
                    "response": response,
                }
            )
    responses = {
        "schema_version": 1,
        "protocol": "calibration-development-responses-v1",
        "split": "development",
        "fixture_sha256": c.digest(c.canonical(fixture)),
        "binary_sha256": BINARY,
        "dataset_id": manifest["dataset_id"],
        "retrieval_sha256": binding,
        "cases": captures,
    }
    return manifest, fixture, responses


def fitted(manifest: dict, fixture: dict, responses: dict) -> dict:
    responses["fixture_sha256"] = c.digest(c.canonical(fixture))
    return c.fit(
        manifest,
        c.canonical(fixture),
        c.canonical(responses),
        binary_sha256=BINARY,
        contract_bytes=CONTRACT,
    )


def test_shared_protocol_and_framed_binding() -> None:
    assert FIXTURE["protocol"] == c.PROTOCOL
    assert FIXTURE["protocol_sha256"] == c.PROTOCOL_SHA256
    assert c.digest(CONTRACT) == c.CONTRACT_SHA256
    for row in FIXTURE["framing_cases"]:
        assert c._frame(row["value"]).hex() == row["frame_hex"]
    binding = FIXTURE["binding"]
    assert c.retrieval_identity(binding["manifest"]) == binding["retrieval_sha256"]
    assert (
        c.scope_identity(binding["entity_ids"]) == binding["eligible_entities_sha256"]
    )
    manifest = deepcopy(binding["manifest"])
    manifest["format_version"] = 5
    manifest["dataset_id"] = "x"
    manifest["recipe_sha256"] = "y"
    manifest["calibration"] = {"sha256": "z"}
    manifest["files"][c.MEMBER] = "z"
    manifest["search"]["semantic_weight"] = 1
    assert c.retrieval_identity(manifest) == binding["retrieval_sha256"]
    manifest["search"]["semantic_weight"] = 1.01
    assert c.retrieval_identity(manifest) != binding["retrieval_sha256"]


def test_search_policy_change_invalidates_fitted_calibration() -> None:
    manifest, fixture, responses = development()
    artifact = fitted(manifest, fixture, responses)
    changed = deepcopy(manifest)
    changed["search"]["version"] = "bm25-minilm-v2"
    assert c.retrieval_identity(changed) != c.retrieval_identity(manifest)
    with pytest.raises(ValueError, match="retrieval"):
        c.parse_artifact(c.canonical(artifact), changed)


@pytest.mark.parametrize("case", FIXTURE["golden"], ids=lambda case: case["id"])
def test_shared_decision_goldens(case: dict) -> None:
    manifest = FIXTURE["binding"]["manifest"]
    assert c.parse_artifact(c.canonical(case["artifact"]), manifest) == case["artifact"]
    assert c.features(case["response"]) == case["features"]
    assert (
        c.decide(case["artifact"], case["context"], case["response"])
        == case["decision"]
    )


@pytest.mark.parametrize(
    "value,expected", [(0.0000005, 1), (-0.0000005, -1), (0.00000049, 0), (-0.0, 0)]
)
def test_rounding(value: float, expected: int) -> None:
    assert c.quantize(value) == expected


@pytest.mark.parametrize("value", [float("nan"), float("inf"), True, "0.5"])
def test_invalid_numeric_features(value: object) -> None:
    response = deepcopy(FIXTURE["golden"][0]["response"])
    response["results"][0]["score"] = value
    with pytest.raises(ValueError, match="finite numeric"):
        c.features(response)


def test_artifact_rejects_unknown_contract_noncanonical_and_duplicate_keys() -> None:
    manifest = FIXTURE["binding"]["manifest"]
    artifact = deepcopy(FIXTURE["golden"][0]["artifact"])
    with pytest.raises(ValueError, match="canonical"):
        c.parse_artifact(c.canonical(artifact) + b"\n", manifest)
    with pytest.raises(ValueError, match="Duplicate"):
        c.parse_artifact(b'{"schema_version":1,"schema_version":1}', manifest)
    artifact["development"]["contract_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="frozen M1"):
        c.validate_artifact(artifact, manifest)


def test_fit_all_seven_profiles_is_deterministic_and_reproducible() -> None:
    manifest, fixture, responses = development(c.PROTOCOL["profiles"])
    artifact = fitted(manifest, fixture, responses)
    assert len(artifact["profiles"]) == 7
    for profile in artifact["profiles"]:
        assert profile["minimums"]["ranking_margin"] == 400_000
        assert all(
            value == 800_000
            for name, value in profile["minimums"].items()
            if name != "ranking_margin"
        )
    assert artifact == fitted(manifest, fixture, responses)
    c.verify_fit(
        artifact,
        manifest,
        c.canonical(fixture),
        c.canonical(responses),
        binary_sha256=BINARY,
        contract_bytes=CONTRACT,
    )
    artifact["profiles"][0]["minimums"]["ranking_margin"] -= 1
    with pytest.raises(ValueError, match="does not reproduce"):
        c.verify_fit(
            artifact,
            manifest,
            c.canonical(fixture),
            c.canonical(responses),
            binary_sha256=BINARY,
            contract_bytes=CONTRACT,
        )


def test_fit_order_independent_thresholds_and_identity_bound_inputs() -> None:
    manifest, fixture, responses = development()
    artifact = fitted(manifest, fixture, responses)
    fixture["cases"].reverse()
    responses["cases"].reverse()
    reordered = fitted(manifest, fixture, responses)
    assert artifact["profiles"] == reordered["profiles"]
    assert artifact["development"] != reordered["development"]


@pytest.mark.parametrize(
    "mutation",
    [
        "evaluation",
        "unreviewed",
        "unfrozen",
        "calibrated",
        "truncated",
        "scope",
        "query",
        "duplicates",
        "missing",
        "mixed_group",
        "too_few_groups",
        "no_positive",
        "no_useful",
        "bad_ranking",
        "nonfinite",
    ],
)
def test_fit_fails_closed(mutation: str) -> None:
    manifest, fixture, responses = development()
    case = fixture["cases"][0]
    response = responses["cases"][0]["response"]
    if mutation == "evaluation":
        case["split"] = "evaluation"
    elif mutation == "unreviewed":
        case["reviewed"] = False
    elif mutation == "unfrozen":
        fixture["status"] = "draft"
    elif mutation == "calibrated":
        response["calibration_status"] = "calibrated"
    elif mutation == "truncated":
        response["results"] = response["results"][:1]
    elif mutation == "scope":
        responses["cases"][0]["eligible_entities_sha256"] = "0" * 64
    elif mutation == "query":
        response["query"] = "changed"
    elif mutation == "duplicates":
        responses["cases"].append(responses["cases"][0])
    elif mutation == "missing":
        responses["cases"].pop()
    elif mutation == "mixed_group":
        fixture["cases"][2]["group_id"] = case["group_id"]
    elif mutation == "too_few_groups":
        for row in fixture["cases"][2:]:
            row["group_id"] = "one-underlying-photograph"
    elif mutation == "no_positive":
        for row in fixture["cases"]:
            row["expected_ids"] = []
    elif mutation == "no_useful":
        for row in responses["cases"][2:]:
            row["response"]["results"] = deepcopy(response["results"])
    elif mutation == "bad_ranking":
        response["results"][1]["score"] = 5.0
    elif mutation == "nonfinite":
        response["results"][0]["score"] = float("nan")
    with pytest.raises(ValueError):
        fitted(manifest, fixture, responses)


def test_fit_rejects_contract_changes_and_final_bundles() -> None:
    manifest, fixture, responses = development()
    with pytest.raises(ValueError, match="frozen M1"):
        c.fit(
            manifest,
            c.canonical(fixture),
            c.canonical(responses),
            binary_sha256=BINARY,
            contract_bytes=CONTRACT + b"\n",
        )
    manifest["format_version"] = 5
    with pytest.raises(ValueError, match="uncalibrated format4"):
        fitted(manifest, fixture, responses)


def test_grid_is_bounded_and_includes_successors() -> None:
    assert c._grid([1, 2, 1_000_000], "semantic_cosine") == [
        -1_000_000,
        1,
        2,
        3,
        1_000_000,
        1_000_001,
    ]
    values = list(range(100))
    grid = c._grid(values, "ranking_margin")
    assert grid == [index * 100 // 63 for index in range(64)]
