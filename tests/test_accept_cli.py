import hashlib
import io
import json
import struct
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from accept_cli import (
    accept_empty_gallery,
    accept_observations,
    accept_search_policy,
    contains_query_path,
    source_probe_scores,
    validate_calibration_response,
    validate_image_response,
    validate_observation_export,
    validate_observation_response,
    validate_text_ranking,
)


def test_calibrated_acceptance_recomputes_shared_protocol_goldens() -> None:
    from pipelines.calibration import canonical, digest

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/calibration.json").read_bytes()
    )
    for case in fixture["golden"]:
        if case["decision"] is None:
            continue
        manifest = deepcopy(fixture["binding"]["manifest"])
        manifest["format_version"] = 5
        manifest["calibration"] = {"sha256": digest(canonical(case["artifact"]))}
        response = {**deepcopy(case["response"]), **case["decision"]}
        assert validate_calibration_response(
            response,
            manifest,
            calibration=case["artifact"],
            eligible_ids=["test:one", "test:two"],
        )
        changed = deepcopy(response)
        changed["decision"]["reason"] = "unsupported assertion"
        with pytest.raises(ValueError, match="recomputed decision"):
            validate_calibration_response(
                changed,
                manifest,
                calibration=case["artifact"],
                eligible_ids=["test:one", "test:two"],
            )


def test_calibration_acceptance_keeps_unsupported_pools_and_base_bundles_strict() -> (
    None
):
    from pipelines.calibration import canonical, digest

    fixture = json.loads(
        (Path(__file__).parent / "fixtures/calibration.json").read_bytes()
    )
    case = fixture["golden"][4]
    manifest = deepcopy(fixture["binding"]["manifest"])
    response = {**deepcopy(case["response"]), **case["decision"]}
    with pytest.raises(ValueError, match="Uncalibrated bundle"):
        validate_calibration_response(response, manifest)
    manifest["format_version"] = 5
    manifest["calibration"] = {"sha256": digest(canonical(case["artifact"]))}
    with pytest.raises(ValueError, match="Unsupported calibration profile"):
        validate_calibration_response(
            response, manifest, calibration=case["artifact"], eligible_ids=["test:one"]
        )
    response.pop("decision")
    response.update(
        match_status="no_supported_match", calibration_status="uncalibrated"
    )
    assert not validate_calibration_response(
        response, manifest, calibration=case["artifact"], eligible_ids=["test:one"]
    )
    manifest["calibration"]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="checksum mismatch"):
        validate_calibration_response(
            response, manifest, calibration=case["artifact"], eligible_ids=["test:one"]
        )


def test_observation_acceptance_allows_only_recomputed_calibrated_decisions() -> None:
    from pipelines.calibration import (
        canonical,
        decide,
        digest,
        retrieval_identity,
        scope_identity,
    )

    manifest, response = ranked_response(enabled=True)
    _, rows, recipes, evidence, images = observation_contract()
    manifest.update(format_version=5, files={})
    golden = json.loads(
        (Path(__file__).parent / "fixtures/calibration.json").read_bytes()
    )["golden"][2]
    fitted = deepcopy(golden["artifact"])
    fitted["retrieval_sha256"] = retrieval_identity(manifest)
    fitted["profiles"][0]["eligible_entities_sha256"] = scope_identity(evidence)
    fitted["profiles"][0]["minimums"] = {"semantic_cosine": 500000, "ranking_margin": 0}
    manifest["calibration"] = {"sha256": digest(canonical(fitted))}
    context = {
        key: value for key, value in fitted["profiles"][0].items() if key != "minimums"
    }
    response.update(decide(fitted, context, response) or {})
    assert response["match_status"] == "candidates"
    assert validate_observation_response(
        response,
        manifest,
        rows,
        recipes,
        evidence,
        enabled=True,
        images=images,
        calibration=fitted,
        eligible_ids=list(evidence),
    )
    response["decision"]["features"]["semantic_cosine"] = 999999
    with pytest.raises(ValueError, match="recomputed decision"):
        validate_observation_response(
            response,
            manifest,
            rows,
            recipes,
            evidence,
            enabled=True,
            images=images,
            calibration=fitted,
            eligible_ids=list(evidence),
        )


def contract() -> dict:
    return {
        "dataset_id": "bundle",
        "image": {
            "vectors": 0,
            "model_sha256": "a" * 64,
            "search": {"calibration": None},
        },
    }


def envelope() -> dict:
    return {
        "dataset_id": "bundle",
        "query_type": "image",
        "query_image_sha256": "hash",
        "match_status": "no_supported_match",
        "calibration_status": "uncalibrated",
        "results": [],
    }


def test_windows_query_path_leaks_are_detected_after_json_decoding() -> None:
    path = r"C:\Users\Researcher\query.png"
    decoded = json.loads(json.dumps({"results": [{"debug": path}]}))
    assert contains_query_path(decoded, path)
    assert contains_query_path({"query_path": path.replace("\\", "/")}, path)
    assert not contains_query_path({"query_image_sha256": "abc"}, path)


def test_uncalibrated_queries_cannot_claim_accepted_matches() -> None:
    response = envelope()
    assert validate_image_response(response, contract(), "hash", False) == []
    response["match_status"] = "candidates"
    with pytest.raises(ValueError, match="uncalibrated"):
        validate_image_response(response, contract(), "hash", False)


def test_combined_queries_require_text_scores_and_contributions() -> None:
    response = envelope()
    response["query_type"] = "image_text"
    item: dict = {
        "id": "source:one",
        "source": "source",
        "score": 0.02,
        "cosine": None,
        "name_match": False,
        "matches": [],
    }
    response["results"] = [item]
    with pytest.raises(ValueError, match="text contribution"):
        validate_image_response(response, contract(), "hash", True)
    item["cosine"] = 0.5
    item["matches"] = [
        {
            "channel": "text",
            "score": 0.5,
            "evidence_id": "page",
            "url": "https://source.test/page",
        }
    ]
    assert validate_image_response(response, contract(), "hash", True) == [item]


def test_empty_gallery_is_exercised_with_an_embedded_probe() -> None:
    body = io.BytesIO()
    expected = b"embedded probe bytes"
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr(
            "image/probes.json",
            json.dumps([{"image_member": "image/probes/sample.png"}]),
        )
        archive.writestr("image/probes/sample.png", expected)
    calls = []

    def run(*args: str) -> bytes:
        assert args[:2] == ("search", "--image")
        assert Path(args[2]).read_bytes() == expected
        calls.append(args)
        response = envelope()
        response["query_image_sha256"] = hashlib.sha256(expected).hexdigest()
        return json.dumps(response).encode()

    with zipfile.ZipFile(body) as archive:
        accept_empty_gallery(run, archive, contract())
    assert len(calls) == 1


def observation_contract() -> tuple[dict, dict, dict, dict, dict]:
    manifest = contract()
    manifest["model"] = {"dimensions": 2}
    manifest["observations"] = {"embedding_model_sha256": "b" * 64}
    recipes = {"recipe": {"model_id": "ocr-model", "model_revision": "pinned"}}
    refs = [{"entity_id": "source:one", "evidence_id": "page", "media_id": "media"}]
    row = {
        "id": "observation",
        "kind": "ocr",
        "origin": "generated",
        "recipe_sha256": "recipe",
        "references": refs,
        "text": "РАДАР <λ>& 1Л122Е",
        "confidence": None,
        "regions": [
            {
                "text": "РАДАР <λ>& 1Л122Е",
                "confidence": 1.0,
                "polygon": [[0.0, 0.1], [1.0, 0.1], [1.0, 0.9], [0.0, 0.9]],
            }
        ],
    }
    evidence = {
        "source:" + name: {"page": "https://source.test/" + name}
        for name in ("one", "two", "three")
    }
    images = {
        "media": {
            "id": "media",
            "vector_index": 0,
            "references": refs,
            "preview": {"member": "image/preview.jpg"},
        }
    }
    return manifest, {row["id"]: row}, recipes, evidence, images


def observation_response(*, enabled: bool = True) -> dict:
    response: dict = {
        "dataset_id": "bundle",
        "query_type": "text",
        "match_status": "no_supported_match" if enabled else "candidates",
        "results": [],
    }
    if enabled:
        response.update(observations=True, calibration_status="uncalibrated")
    item: dict = {
        "id": "source:one",
        "source": "source",
        "score": 1.0,
        "cosine": 1.0,
        "name_match": False,
    }
    match: dict = {
        "channel": "text",
        "score": 1.0,
        "evidence_id": "page",
        "url": "https://source.test/one",
    }
    if enabled:
        match.update(
            channel="ocr",
            origin="generated",
            observation_id="observation",
            media_id="media",
            recipe_sha256="recipe",
            model_id="ocr-model",
            model_revision="pinned",
            embedding_model_sha256="b" * 64,
        )
    item["matches"] = [match]
    response["results"] = [item]
    return response


def ranked_response(
    *, enabled: bool = False, lexical: bool = True
) -> tuple[dict, dict]:
    manifest = observation_contract()[0]
    manifest.update(
        entities=3,
        search={
            "version": "bm25-minilm-v1",
            "k1": 1.2,
            "b": 0.75,
            "rank_constant": 60,
            "semantic_weight": 0.5,
            "lexical_weight": 2.0,
            "captions": 1,
        },
    )
    response = observation_response(enabled=enabled)
    response.update(
        mode="hybrid", ranking_policy=manifest["search"], score_kind="ranking_signal"
    )
    item = response["results"][0]
    semantic = item["matches"][0]
    item["cosine"] = semantic["score"] = 0.8
    semantic.update(method="semantic", reason="ocr" if enabled else "source_text")
    score = 0.5 / 62
    item["ranking"] = {
        "method": "bm25-minilm-v1",
        "semantic_rank": 2,
        "text_score": score,
    }
    if lexical:
        contribution = deepcopy(semantic)
        contribution.update(method="lexical", score=4.25, terms=["radar"])
        item["matches"].append(contribution)
        score += 2.0 / 61
        item["ranking"].update(lexical_rank=1, text_score=score)
    item["score"] = score
    return manifest, response


@pytest.mark.parametrize("lexical", [False, True])
def test_ranked_text_keeps_cosine_and_bm25_separate_from_weighted_fusion(
    lexical: bool,
) -> None:
    manifest, response = ranked_response(lexical=lexical)
    item = response["results"][0]
    expected = 0.5 / 62 + (2.0 / 61 if lexical else 0)
    assert validate_text_ranking(item, manifest) == pytest.approx(expected)
    assert item["matches"][0]["score"] == 0.8
    if lexical:
        assert item["matches"][1]["score"] == 4.25


@pytest.mark.parametrize("cosine", [-1.000001, -0.5, 0.8, 1.000001])
def test_ranked_source_name_priority_keeps_raw_cosine_signal(cosine: float) -> None:
    manifest, response = ranked_response()
    item = response["results"][0]
    item.update(name_match=True, cosine=cosine, score=2 + min(1, max(-1, cosine)))
    item["matches"][0]["score"] = cosine
    item["ranking"]["text_score"] = item["score"]
    assert validate_text_ranking(item, manifest) == pytest.approx(item["score"])


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("ranking",), None),
        (("ranking", "method"), "unsupported-v2"),
        (("ranking", "semantic_rank"), 0),
        (("ranking", "semantic_rank"), True),
        (("ranking", "semantic_rank"), 4),
        (("ranking", "lexical_rank"), -1),
        (("ranking", "lexical_rank"), 1.0),
        (("ranking", "lexical_rank"), 4),
        (("ranking", "lexical_rank"), 0),
        (("ranking", "text_score"), 0.8),
        (("ranking", "text_score"), float("nan")),
        (("name_match",), "false"),
        (("cosine",), float("inf")),
        (("cosine",), 0.9),
        (("matches", 0, "method"), "lexical"),
        (("matches", 0, "score"), True),
        (("matches", 0, "reason"), True),
        (("matches", 0, "reason"), " "),
        (("matches", 1, "reason"), ""),
        (("matches", 1, "score"), 0),
        (("matches", 1, "score"), float("nan")),
        (("matches", 1, "terms"), []),
        (("matches", 1, "terms"), "radar"),
        (("matches", 1, "terms"), [""]),
        (("matches", 1, "terms"), [1]),
        (("matches", 1, "terms"), [" "]),
        (("matches", 1, "terms"), ["radar"] * 1001),
    ],
)
def test_ranked_text_rejects_tampered_contributions(
    path: tuple[str | int, ...], value: object
) -> None:
    manifest, response = ranked_response()
    item = response["results"][0]
    parent = item
    for key in path[:-1]:
        parent = parent[key]
    parent[path[-1]] = value
    with pytest.raises(ValueError):
        validate_text_ranking(item, manifest)


@pytest.mark.parametrize("field", ["semantic_rank", "lexical_rank", "name_match"])
def test_forged_ranks_or_name_signal_fail_even_with_consistent_arithmetic(
    field: str,
) -> None:
    manifest, response = ranked_response()
    item = response["results"][0]
    if field == "name_match":
        item["name_match"] = "true"
        item["ranking"]["text_score"] = 2.8
    else:
        item["ranking"][field] = 4
        item["ranking"]["text_score"] = 0.5 / (
            60 + item["ranking"]["semantic_rank"]
        ) + 2.0 / (60 + item["ranking"]["lexical_rank"])
    with pytest.raises(ValueError):
        validate_text_ranking(item, manifest)


@pytest.mark.parametrize(
    "failure", [None, "status", "policy", "score_kind", "lexical", "name", "generated"]
)
def test_acceptance_exercises_source_query_without_lexical_support(
    failure: str | None,
) -> None:
    manifest, response = ranked_response(lexical=False)
    response.update(
        match_status="no_supported_match", calibration_status="uncalibrated"
    )
    item = response["results"][0]
    if failure == "status":
        response["match_status"] = "candidates"
    elif failure == "policy":
        response["ranking_policy"] = {**manifest["search"], "semantic_weight": 1.0}
    elif failure == "score_kind":
        response["score_kind"] = "identity_probability"
    elif failure == "lexical":
        item["ranking"]["lexical_rank"] = 1
        item["ranking"]["text_score"] += 2 / 61
        item["score"] = item["ranking"]["text_score"]
        item["matches"].append(
            {
                **item["matches"][0],
                "method": "lexical",
                "score": 1.0,
                "terms": ["invented"],
            }
        )
    elif failure == "name":
        item["name_match"] = True
        item["score"] = item["ranking"]["text_score"] = 2.8
    elif failure == "generated":
        item["matches"][0]["origin"] = "generated"
    calls = []

    def run(*args: str) -> bytes:
        calls.append(args)
        return json.dumps(response).encode()

    if failure:
        with pytest.raises(ValueError):
            accept_search_policy(run, manifest)
    else:
        accept_search_policy(run, manifest)
    assert calls == [("search", "--limit", "3", "zzqvxjknobberplux")]
    calls.clear()
    accept_search_policy(run, contract())
    assert calls == []


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("combined", [False, True])
def test_ranked_observations_keep_both_text_provenances_and_optional_image(
    enabled: bool, combined: bool
) -> None:
    manifest, response = ranked_response(enabled=enabled)
    _, rows, recipes, evidence, images = observation_contract()
    item = response["results"][0]
    if enabled:
        item["matches"][0] = {
            "channel": "text",
            "method": "semantic",
            "reason": "source_text",
            "score": 0.8,
            "evidence_id": "page",
            "url": "https://source.test/one",
        }
    if combined:
        response.update(query_type="image_text", query_image_sha256="hash")
        item["score"] = 2 / 61
        item["matches"].append(
            {
                "channel": "image",
                "score": 0.9,
                "evidence_id": "page",
                "url": "https://source.test/one",
                "media_id": "media",
                "model_sha256": "a" * 64,
            }
        )
    assert validate_observation_response(
        response,
        manifest,
        rows,
        recipes,
        evidence,
        enabled=enabled,
        image_sha256="hash" if combined else None,
        images=images,
    ) == [item]
    item["matches"][1]["evidence_id"] = "unrelated"
    with pytest.raises(ValueError, match="evidence"):
        validate_observation_response(
            response,
            manifest,
            rows,
            recipes,
            evidence,
            enabled=enabled,
            image_sha256="hash" if combined else None,
            images=images,
        )


def test_ranked_generated_lexical_match_requires_the_same_recipe_and_opt_in() -> None:
    manifest, response = ranked_response(enabled=True)
    _, rows, recipes, evidence, _ = observation_contract()
    validate_observation_response(
        response, manifest, rows, recipes, evidence, enabled=True
    )
    item = response["results"][0]
    item["matches"][1]["recipe_sha256"] = "other-recipe"
    with pytest.raises(ValueError, match="provenance"):
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=True
        )
    item["matches"][1]["recipe_sha256"] = "recipe"
    response.pop("observations")
    with pytest.raises(ValueError, match="generated"):
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=False
        )


def test_source_caption_lexical_evidence_is_not_a_generated_observation() -> None:
    manifest, response = ranked_response()
    _, rows, recipes, evidence, _ = observation_contract()
    caption = response["results"][0]["matches"][1]
    caption.update(reason="source_caption", media_id="source:media:" + "a" * 24)
    validate_observation_response(
        response, manifest, rows, recipes, evidence, enabled=False
    )
    caption.update(origin="generated", observation_id="observation")
    with pytest.raises(ValueError, match="source contribution"):
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=False
        )


@pytest.mark.parametrize("lexical", [False, True])
def test_ranked_combined_source_queries_preserve_text_channel_score(
    lexical: bool,
) -> None:
    manifest, response = ranked_response(lexical=lexical)
    response.update(
        query_type="image_text",
        query_image_sha256="hash",
        match_status="no_supported_match",
        calibration_status="uncalibrated",
    )
    item = response["results"][0]
    item["score"] = 1 / 61
    assert validate_image_response(response, manifest, "hash", True) == [item]
    item["ranking"]["text_score"] = item["score"]
    with pytest.raises(ValueError, match="ranking score"):
        validate_image_response(response, manifest, "hash", True)


def test_new_policy_does_not_apply_lexical_ranking_to_vector_mode() -> None:
    manifest, _ = ranked_response()
    response = observation_response(enabled=False)
    response["mode"] = "vector"
    _, rows, recipes, evidence, _ = observation_contract()
    assert validate_observation_response(
        response, manifest, rows, recipes, evidence, enabled=False
    )
    response["mode"] = "hybrid"
    with pytest.raises(ValueError, match="ranking policy"):
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=False
        )


@pytest.mark.parametrize(
    "failure",
    [
        "url",
        "owner",
        "embedding",
        "score",
        "cosine",
        "contributions",
        "source_channel",
        "default_opt_in",
    ],
)
def test_generated_response_requires_exact_provenance_and_opt_in(failure: str) -> None:
    manifest, rows, recipes, evidence, _ = observation_contract()
    response = observation_response()
    validate_observation_response(
        response, manifest, rows, recipes, evidence, enabled=True
    )
    match = response["results"][0]["matches"][0]
    enabled = True
    if failure == "url":
        match["url"] = "https://unrelated.test/page"
    elif failure == "owner":
        match["media_id"] = "other-media"
    elif failure == "embedding":
        match["embedding_model_sha256"] = "c" * 64
    elif failure == "score":
        response["results"][0]["score"] = float("nan")
    elif failure == "cosine":
        response["results"][0]["cosine"] = 0.5
    elif failure == "contributions":
        response["results"][0]["matches"] = []
    elif failure == "source_channel":
        match["channel"] = "text"
    else:
        enabled = False
        response = observation_response(enabled=False)
        response["observations"] = True
    with pytest.raises(ValueError):
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=enabled
        )


def test_combined_observations_require_image_ownership() -> None:
    manifest, rows, recipes, evidence, images = observation_contract()
    response = observation_response()
    response.update(query_type="image_text", query_image_sha256="hash")
    response["results"][0]["matches"].append(
        {
            "channel": "image",
            "score": 0.9,
            "evidence_id": "page",
            "url": "https://source.test/one",
            "media_id": "media",
            "model_sha256": "a" * 64,
        }
    )
    validate_observation_response(
        response,
        manifest,
        rows,
        recipes,
        evidence,
        enabled=True,
        image_sha256="hash",
        images=images,
    )
    images["media"]["references"] = [{"entity_id": "source:two", "evidence_id": "page"}]
    with pytest.raises(ValueError, match="image contribution"):
        validate_observation_response(
            response,
            manifest,
            rows,
            recipes,
            evidence,
            enabled=True,
            image_sha256="hash",
            images=images,
        )


@pytest.mark.parametrize("combined", [False, True])
def test_text_contribution_keeps_source_name_boost_separate_from_cosine(
    combined: bool,
) -> None:
    manifest, rows, recipes, evidence, images = observation_contract()
    response = observation_response()
    item = response["results"][0]
    item.update(name_match=True, score=0.025 if combined else 3.0)
    item["matches"][0]["score"] = 3.0
    if combined:
        response.update(query_type="image_text", query_image_sha256="hash")
    validate_observation_response(
        response,
        manifest,
        rows,
        recipes,
        evidence,
        enabled=True,
        image_sha256="hash" if combined else None,
        images=images,
    )
    item["matches"][0]["score"] = 1.0
    with pytest.raises(ValueError, match="ranking score"):
        validate_observation_response(
            response,
            manifest,
            rows,
            recipes,
            evidence,
            enabled=True,
            image_sha256="hash" if combined else None,
            images=images,
        )


@pytest.mark.parametrize(
    "failure",
    [
        "numeric_identity",
        "content",
        "recipe",
        "polygon",
        "confidence",
        "description_confidence",
    ],
)
def test_observation_inspection_preserves_raw_identity_and_validates_regions(
    failure: str,
) -> None:
    manifest, rows, recipes, _, _ = observation_contract()
    expected = rows["observation"]
    response: dict = {
        "dataset_id": "bundle",
        "entity_id": "source:one",
        "observations": [deepcopy(expected)],
        "recipes": deepcopy(recipes),
    }
    validate_observation_export(response, manifest, "source:one", expected, recipes)
    row = response["observations"][0]
    if failure == "numeric_identity":
        row["regions"][0]["confidence"] = 1
    elif failure == "content":
        row["text"] = "ASCII transliteration"
    elif failure == "recipe":
        response["recipes"]["recipe"]["model_revision"] = "other"
    else:
        if failure == "polygon":
            row["regions"][0]["polygon"][0][0] = 1.1
        elif failure == "confidence":
            row["regions"][0]["confidence"] = True
        else:
            row.update(kind="description", regions=[], confidence=0.9)
        expected = deepcopy(row)
    with pytest.raises(ValueError):
        validate_observation_export(response, manifest, "source:one", expected, recipes)


def observation_archive(*, empty: bool = False) -> bytes:
    _, rows, recipes, evidence, images = observation_contract()
    if empty:
        rows, recipes = {}, {}
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        members = {
            "index.json": [{"id": identifier} for identifier in evidence],
            "chunks.json": [{"entity": index} for index in (0, 1, 2, 0)],
            "probes.json": ["probe"],
            "observations/index.json": list(rows.values()),
            "observations/recipes.json": recipes,
            "observations/chunks.json": [
                {"observation_id": row["id"], "text": row["text"]}
                for row in rows.values()
            ],
            "image/index.json": list(images.values()),
        }
        for identifier, pages in evidence.items():
            members["entities/" + identifier.replace(":", "/") + ".json"] = {
                "evidence": [{"id": key, "url": value} for key, value in pages.items()]
            }
        for name, value in members.items():
            archive.writestr(name, json.dumps(value))
        archive.writestr(
            "vectors.f32", struct.pack("<8f", 1, 0, 0.8, 0.6, 0, 1, 0.6, 0.8)
        )
        archive.writestr("probes.f32", struct.pack("<2f", 1, 0))
        archive.writestr("image/preview.jpg", b"preview pixels")
    return body.getvalue()


@pytest.mark.parametrize(
    "failure", [None, "ranking", "ignored_extension", "default_changed", "empty"]
)
def test_acceptance_executes_inspection_opt_in_and_original_vector_oracle(
    failure: str | None,
) -> None:
    manifest, rows, recipes, _, _ = observation_contract()
    calls: list[tuple[str, ...]] = []
    baselines = 0

    def run(*args: str) -> bytes:
        nonlocal baselines
        calls.append(args)
        if args[0] == "observations":
            assert args[-1] == "source:one"
            response: dict = {
                "dataset_id": "bundle",
                "entity_id": "source:one",
                "observations": [] if failure == "empty" else list(rows.values()),
                "recipes": {} if failure == "empty" else recipes,
            }
        else:
            enabled = "--observations" in args
            response = observation_response(enabled=enabled)
            if not enabled:
                baselines += 1
                for name, score in (("two", 0.8), ("three", 0.0)):
                    item = deepcopy(response["results"][0])
                    item.update(id="source:" + name, score=score, cosine=score)
                    item["matches"][0].update(
                        score=score, url="https://source.test/" + name
                    )
                    response["results"].append(item)
                if failure == "ranking":
                    response["results"].reverse()
                if failure == "default_changed" and baselines == 2:
                    response["debug"] = "changed"
            elif failure == "empty":
                response["results"][0]["matches"] = observation_response(enabled=False)[
                    "results"
                ][0]["matches"]
            elif failure == "ignored_extension":
                response["results"][0]["cosine"] = 0.5
                response["results"][0]["matches"][0]["score"] = 0.5
            if "--image" in args:
                picture = Path(args[args.index("--image") + 1])
                response.update(
                    query_type="image_text",
                    query_image_sha256=hashlib.sha256(picture.read_bytes()).hexdigest(),
                )
                response["results"][0]["matches"].append(
                    {
                        "channel": "image",
                        "score": 0.9,
                        "evidence_id": "page",
                        "url": "https://source.test/one",
                        "media_id": "media",
                        "model_sha256": "a" * 64,
                    }
                )
        return json.dumps(response).encode()

    with zipfile.ZipFile(
        io.BytesIO(observation_archive(empty=failure == "empty"))
    ) as archive:
        assert source_probe_scores(archive, manifest) == pytest.approx(
            {"source:one": 1, "source:two": 0.8, "source:three": 0}
        )
        if failure not in {None, "empty"}:
            with pytest.raises(ValueError):
                accept_observations(run, archive, manifest)
        else:
            accept_observations(run, archive, manifest)
            assert baselines == 2
            assert any(call[0] == "observations" for call in calls)
            assert any("--observations" in call for call in calls)
            assert (any("--image" in call for call in calls)) is (failure != "empty")
