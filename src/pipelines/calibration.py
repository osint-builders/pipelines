"""Deterministic development-only acceptance rules over frozen retrieval signals."""

import hashlib
import itertools
import json
import math
import re
import struct
from collections import defaultdict
from collections.abc import Iterable
from typing import Any

PROTOCOL: dict[str, Any] = {
    "schema_version": 1,
    "feature_version": "candidate-signals-v1",
    "fit_version": "bounded-threshold-grid-v1",
    "member": "calibration.json",
    "scale": 1000000,
    "rounding": "half-away-from-zero",
    "score_bounds": {"cosine": [-1000000, 1000000], "ranking_margin": [0, 4000000]},
    "threshold_grid": {
        "maximum_values_per_feature": 64,
        "selection": "sorted unique observed integers and their successors plus lower bound; if over 64 select floor(i*(n-1)/63), i=0..63",
    },
    "features": {
        "text": ["semantic_cosine", "ranking_margin"],
        "image": ["image_cosine", "ranking_margin"],
        "image_text": ["image_cosine", "semantic_cosine", "ranking_margin"],
    },
    "profiles": [
        {"query_type": "text", "text_mode": "hybrid", "observations": False},
        {"query_type": "text", "text_mode": "vector", "observations": False},
        {"query_type": "text", "text_mode": "hybrid", "observations": True},
        {"query_type": "text", "text_mode": "vector", "observations": True},
        {"query_type": "image", "text_mode": None, "observations": False},
        {"query_type": "image_text", "text_mode": "hybrid", "observations": False},
        {"query_type": "image_text", "text_mode": "hybrid", "observations": True},
    ],
    "binding": "SHA-256(prefix calibration-retrieval-v1 followed by LF, then framed manifest); remove dataset_id, recipe_sha256, calibration and files/calibration.json; normalize format_version5 to4",
    "scope": "SHA-256(prefix calibration-scope-v1 followed by LF, then framed sorted unique eligible source-qualified entity IDs)",
    "margin": "max(0, first score minus second score); zero if no second result; extract before user limit",
    "support": "semantic_cosine is first result cosine; image_cosine is maximum first-result image match score; clamp cosine to [-1,1] before rounding",
    "missing_features": "abstain; empty result also abstains; missing profile preserves legacy behavior",
    "rule": "accept iff all integer feature values >= their integer minimums",
    "objective": [
        "maximum accepted correct top1",
        "maximum accepted correct top5",
        "minimum accepted negative groups",
        "minimum accepted confusable wrong-top1",
        "lexicographically greatest minimums in declared feature order",
    ],
    "negative_unit": "case.group_id; all correlated cases in a group must be negative and the group is falsely accepted if any member is accepted",
    "positive_unit": "case.group_id; every case in a group must be accepted and correct to count for the objective",
    "constraints": "tracked M1 no_match_per_modality and confusable_variant thresholds; each profile needs positive and negative groups and at least one accepted correct positive group",
    "fit_input_status": "reviewed_frozen_development",
    "response_protocol": "calibration-development-responses-v1",
    "framing": {
        "null": "n",
        "false": "f",
        "true": "t",
        "number": "d + 16 lowercase hexadecimal IEEE754 binary64 big-endian bytes + semicolon; normalize negative zero; finite abs<=2^53-1",
        "string": "s + ASCII decimal UTF8 byte length + colon + raw UTF8",
        "array": "a + ASCII decimal item count + colon + item frames",
        "object": "o + ASCII decimal key count + colon + key/value frames; UTF8-byte-sorted string keys",
    },
    "limits": {
        "member_bytes": 65536,
        "profiles": 256,
        "threshold_successor_above_feature_max": 1,
    },
    "artifact_fields": [
        "schema_version",
        "feature_version",
        "fit_version",
        "protocol_sha256",
        "retrieval_sha256",
        "development",
        "profiles",
    ],
    "development_fields": [
        "fixture_sha256",
        "responses_sha256",
        "binary_sha256",
        "contract_sha256",
    ],
    "profile_fields": [
        "query_type",
        "text_mode",
        "observations",
        "eligible_entities_sha256",
        "minimums",
    ],
    "decision": "matching profile returns match_status candidates or no_supported_match, calibration_status calibrated, decision artifact_sha256/profile_id/reason/features/minimums; absent profile returns no override",
    "development_fixture": {
        "schema_version": 1,
        "status": "reviewed_frozen_development",
        "retrieval_sha256": "required",
        "scopes": "map scope name to sorted unique eligible entity IDs; must be subsets of bundle entities",
        "cases": "id,split=development,reviewed=true,label_basis,group_id,scope,query_type,text_mode,observations,query{text,image_sha256},expected_ids,confusable_ids; reviewed negatives have empty expected_ids",
    },
    "development_responses": {
        "schema_version": 1,
        "protocol": "calibration-development-responses-v1",
        "split": "development",
        "fixture_sha256": "raw fixture bytes",
        "binary_sha256": "actual calibration runner executable",
        "dataset_id": "uncalibrated input bundle ID",
        "retrieval_sha256": "required",
        "cases": "one case_id,eligible_entities_sha256,response per fixture case; complete uncropped first two candidates (or full population if smaller), never calibrated response",
    },
}
SCALE = 1_000_000
MEMBER = "calibration.json"
MAX_BYTES = 65_536
MAX_PROFILES = 256
FEATURE_VERSION = "candidate-signals-v1"
FIT_VERSION = "bounded-threshold-grid-v1"
CONTRACT_SHA256 = "3460166733affac363e071d5b8fbb7f8ae267d7c2ff323a033ae2356a83c63f9"
_HASH = re.compile(r"[a-f0-9]{64}")
_CONTEXT_KEYS = {"query_type", "text_mode", "observations", "eligible_entities_sha256"}
_FEATURES = {
    "text": ("semantic_cosine", "ranking_margin"),
    "image": ("image_cosine", "ranking_margin"),
    "image_text": ("image_cosine", "semantic_cosine", "ranking_margin"),
}


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


PROTOCOL_SHA256 = digest(canonical(PROTOCOL))


def _frame(value: object) -> bytes:
    if value is None:
        return b"n"
    if type(value) is bool:
        return b"t" if value else b"f"
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number) or abs(number) > 2**53 - 1:
            raise ValueError("Invalid binding number")
        return b"d" + struct.pack(">d", number if number else 0.0).hex().encode() + b";"
    if isinstance(value, str):
        encoded = value.encode("utf-8")
        return b"s" + str(len(encoded)).encode() + b":" + encoded
    if isinstance(value, list):
        return (
            b"a"
            + str(len(value)).encode()
            + b":"
            + b"".join(_frame(item) for item in value)
        )
    if isinstance(value, dict) and all(isinstance(key, str) for key in value):
        keys = sorted(value, key=lambda key: key.encode("utf-8"))
        return (
            b"o"
            + str(len(keys)).encode()
            + b":"
            + b"".join(_frame(key) + _frame(value[key]) for key in keys)
        )
    raise ValueError("Invalid binding value")


def retrieval_identity(manifest: dict) -> str:
    value = {
        key: item
        for key, item in manifest.items()
        if key not in {"dataset_id", "recipe_sha256", "calibration"}
    }
    value["files"] = {
        key: item for key, item in manifest["files"].items() if key != MEMBER
    }
    if value["format_version"] == 5:
        value["format_version"] = 4
    return digest(b"calibration-retrieval-v1\n" + _frame(value))


def scope_identity(entity_ids: Iterable[str]) -> str:
    identities = list(entity_ids)
    if not all(
        isinstance(item, str) and ":" in item and all(item.split(":", 1))
        for item in identities
    ):
        raise ValueError("Scope requires source-qualified entity IDs")
    return digest(b"calibration-scope-v1\n" + _frame(sorted(set(identities))))


def _hash(value: object) -> bool:
    return isinstance(value, str) and _HASH.fullmatch(value) is not None


def _context(context: dict) -> None:
    if (
        set(context) != _CONTEXT_KEYS
        or type(context["observations"]) is not bool
        or not _hash(context["eligible_entities_sha256"])
    ):
        raise ValueError("Invalid calibration profile context")
    mode = {key: context[key] for key in ("query_type", "text_mode", "observations")}
    if mode not in PROTOCOL["profiles"]:
        raise ValueError("Unsupported calibration query mode")


def profile_id(context: dict) -> str:
    _context(context)
    return digest(canonical(context))


def _bounds(name: str) -> tuple[int, int]:
    return (0, 4 * SCALE) if name == "ranking_margin" else (-SCALE, SCALE)


def validate_artifact(artifact: dict, manifest: dict) -> None:
    if (
        set(artifact) != set(PROTOCOL["artifact_fields"])
        or type(artifact.get("schema_version")) is not int
        or artifact["schema_version"] != 1
    ):
        raise ValueError("Unsupported calibration artifact")
    if (
        artifact["feature_version"] != FEATURE_VERSION
        or artifact["fit_version"] != FIT_VERSION
        or artifact["protocol_sha256"] != PROTOCOL_SHA256
        or artifact["retrieval_sha256"] != retrieval_identity(manifest)
    ):
        raise ValueError("Calibration version or retrieval binding mismatch")
    development = artifact["development"]
    if (
        not isinstance(development, dict)
        or set(development) != set(PROTOCOL["development_fields"])
        or not all(_hash(value) for value in development.values())
    ):
        raise ValueError("Calibration requires complete development proof identities")
    if development["contract_sha256"] != CONTRACT_SHA256:
        raise ValueError("Calibration acceptance contract differs from frozen M1")
    profiles = artifact["profiles"]
    if not isinstance(profiles, list) or not 1 <= len(profiles) <= MAX_PROFILES:
        raise ValueError("Invalid calibration profile count")
    previous = ""
    for profile in profiles:
        if not isinstance(profile, dict) or set(profile) != _CONTEXT_KEYS | {
            "minimums"
        }:
            raise ValueError("Invalid calibration profile")
        context = {key: profile[key] for key in _CONTEXT_KEYS}
        identity = profile_id(context)
        if identity <= previous:
            raise ValueError(
                "Calibration profiles must be unique and sorted by profile ID"
            )
        previous = identity
        minimums = profile["minimums"]
        if not isinstance(minimums, dict) or set(minimums) != set(
            _FEATURES[context["query_type"]]
        ):
            raise ValueError("Invalid calibration feature thresholds")
        for key, value in minimums.items():
            lower, upper = _bounds(key)
            if type(value) is not int or not lower <= value <= upper + 1:
                raise ValueError("Invalid calibration threshold value")
    if len(canonical(artifact)) > MAX_BYTES:
        raise ValueError("Calibration artifact exceeds size limit")


def _object(pairs: list[tuple[str, Any]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate calibration JSON key")
    return result


def _decode(body: bytes) -> dict:
    value = json.loads(body, object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ValueError("Calibration document must be an object")
    return value


def parse_artifact(body: bytes, manifest: dict) -> dict:
    if len(body) > MAX_BYTES:
        raise ValueError("Calibration artifact exceeds size limit")
    artifact = _decode(body)
    validate_artifact(artifact, manifest)
    if canonical(artifact) != body:
        raise ValueError("Calibration member must use canonical JSON")
    return artifact


def _number(value: object) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
    ):
        raise ValueError("Calibration features require finite numeric scores")
    return float(value)


def quantize(value: float) -> int:
    number = _number(value)
    magnitude = math.floor(abs(number) * SCALE + 0.5)
    return -magnitude if number < 0 else magnitude


def response_context(response: dict, eligible_entities_sha256: str) -> dict:
    kind = response.get("query_type")
    mode = response.get("mode")
    if (kind == "text" and mode not in {"hybrid", "vector"}) or (
        kind in {"image", "image_text"} and mode != kind
    ):
        raise ValueError("Response mode does not match query type")
    context = {
        "query_type": kind,
        "text_mode": mode
        if kind == "text"
        else ("hybrid" if kind == "image_text" else None),
        "observations": response.get("observations", False),
        "eligible_entities_sha256": eligible_entities_sha256,
    }
    _context(context)
    return context


def features(response: dict) -> dict[str, int] | None:
    kind = response_context(response, "0" * 64)["query_type"]
    results = response.get("results")
    if not isinstance(results, list):
        raise ValueError("Calibration response requires results")
    if not results:
        return None
    first = results[0]
    score = _number(first["score"])
    margin = score - _number(results[1]["score"]) if len(results) > 1 else 0.0
    if margin < -1e-12 or margin > 4:
        raise ValueError("Calibration response ranking is not ordered")
    result = {"ranking_margin": quantize(max(0, margin))}
    if kind in {"text", "image_text"}:
        if first.get("cosine") is None:
            return None
        result["semantic_cosine"] = quantize(max(-1, min(1, _number(first["cosine"]))))
    if kind in {"image", "image_text"}:
        matches = [
            match
            for match in first.get("matches", [])
            if match.get("channel") == "image"
        ]
        if not matches:
            return None
        result["image_cosine"] = quantize(
            max(-1, min(1, max(_number(match["score"]) for match in matches)))
        )
    return result


def decide(artifact: dict, context: dict, response: dict) -> dict | None:
    identity = profile_id(context)
    if response_context(response, context["eligible_entities_sha256"]) != context:
        raise ValueError("Calibration context differs from response")
    profile = next(
        (
            row
            for row in artifact["profiles"]
            if profile_id({key: row[key] for key in _CONTEXT_KEYS}) == identity
        ),
        None,
    )
    if profile is None:
        return None
    signals = features(response)
    accepted = signals is not None and all(
        signals[key] >= value for key, value in profile["minimums"].items()
    )
    reason = (
        "accepted"
        if accepted
        else "below_threshold"
        if signals is not None
        else "missing_features"
        if response["results"]
        else "no_results"
    )
    return {
        "match_status": "candidates" if accepted else "no_supported_match",
        "calibration_status": "calibrated",
        "decision": {
            "artifact_sha256": digest(canonical(artifact)),
            "profile_id": identity,
            "reason": reason,
            "features": signals,
            "minimums": profile["minimums"],
        },
    }


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _identities(value: object, pool: set[str]) -> set[str]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError("Calibration labels require entity ID lists")
    if len(set(value)) != len(value) or not set(value) <= pool:
        raise ValueError("Calibration labels contain duplicate or ineligible entities")
    return set(value)


def _rows(
    manifest: dict,
    fixture: dict,
    responses: dict,
    fixture_sha256: str,
    binary_sha256: str,
) -> dict[str, list[dict]]:
    binding = retrieval_identity(manifest)
    if (
        fixture.get("schema_version") != 1
        or fixture.get("status") != "reviewed_frozen_development"
        or fixture.get("retrieval_sha256") != binding
    ):
        raise ValueError(
            "Calibration requires a frozen development fixture for this retrieval identity"
        )
    required = {
        "schema_version": 1,
        "protocol": "calibration-development-responses-v1",
        "split": "development",
        "fixture_sha256": fixture_sha256,
        "binary_sha256": binary_sha256,
        "dataset_id": manifest["dataset_id"],
        "retrieval_sha256": binding,
    }
    if any(responses.get(key) != value for key, value in required.items()):
        raise ValueError("Calibration development response identity mismatch")
    scopes = fixture.get("scopes")
    if not isinstance(scopes, dict) or not scopes:
        raise ValueError("Calibration requires explicit eligible scopes")
    known = {
        name[len("entities/") : -len(".json")].replace("/", ":", 1)
        for name in manifest["files"]
        if name.startswith("entities/") and name.endswith(".json")
    }
    for name, values in scopes.items():
        if not _nonempty(name) or not isinstance(values, list) or not values:
            raise ValueError("Calibration scope must be a named nonempty entity list")
        scope_identity(values)
        if values != sorted(set(values)) or not set(values) <= known:
            raise ValueError(
                "Calibration scope must be sorted, unique, and belong to the bundle"
            )
    cases = fixture.get("cases")
    captures = responses.get("cases")
    if not isinstance(cases, list) or not cases or not isinstance(captures, list):
        raise ValueError("Calibration requires development cases and responses")
    captured = {}
    for row in captures:
        if (
            not isinstance(row, dict)
            or not _nonempty(row.get("case_id"))
            or row["case_id"] in captured
        ):
            raise ValueError("Calibration response case IDs must be unique")
        captured[row["case_id"]] = row
    seen: set[str] = set()
    profiles: dict[str, list[dict]] = defaultdict(list)
    for case in cases:
        if (
            not isinstance(case, dict)
            or not _nonempty(case.get("id"))
            or case["id"] in seen
        ):
            raise ValueError("Calibration fixture case IDs must be unique")
        seen.add(case["id"])
        if (
            case.get("split") != "development"
            or case.get("reviewed") is not True
            or not _nonempty(case.get("label_basis"))
            or not _nonempty(case.get("group_id"))
        ):
            raise ValueError(
                "Calibration accepts reviewed development cases with group and label evidence only"
            )
        scope = case.get("scope")
        if not isinstance(scope, str) or scope not in scopes:
            raise ValueError("Calibration case refers to an unknown scope")
        pool = set(scopes[scope])
        eligible = scope_identity(pool)
        context = {
            key: case.get(key) for key in ("query_type", "text_mode", "observations")
        }
        context["eligible_entities_sha256"] = eligible
        identity = profile_id(context)
        query = case.get("query")
        if not isinstance(query, dict) or not isinstance(query.get("text"), str):
            raise ValueError("Calibration case requires explicit query text")
        if (context["query_type"] == "image" and query["text"] != "") or (
            context["query_type"] != "image" and not query["text"].strip()
        ):
            raise ValueError("Calibration query text does not match its modality")
        image = query.get("image_sha256")
        if (context["query_type"] == "text" and image is not None) or (
            context["query_type"] != "text" and not _hash(image)
        ):
            raise ValueError("Calibration query image does not match its modality")
        expected = _identities(case.get("expected_ids"), pool)
        confusable = _identities(case.get("confusable_ids"), pool)
        if expected & confusable:
            raise ValueError("Calibration expected and confusable labels overlap")
        capture = captured.get(case["id"], {})
        response = capture.get("response")
        if capture.get("eligible_entities_sha256") != eligible or not isinstance(
            response, dict
        ):
            raise ValueError("Calibration response scope is missing or mismatched")
        if (
            response_context(response, eligible) != context
            or response.get("dataset_id") != manifest["dataset_id"]
            or response.get("query", "") != query["text"]
            or response.get("query_image_sha256") != image
        ):
            raise ValueError("Calibration response query or dataset identity mismatch")
        if (
            "decision" in response
            or response.get("calibration_status", "uncalibrated") != "uncalibrated"
        ):
            raise ValueError("Calibration fitting requires uncalibrated responses")
        results = response.get("results")
        if not isinstance(results, list) or len(results) < min(2, len(pool)):
            raise ValueError(
                "Calibration fitting requires the complete first two candidates"
            )
        result_ids = [
            row.get("id") if isinstance(row, dict) else None for row in results
        ]
        if (
            any(not isinstance(item, str) for item in result_ids)
            or len(set(result_ids)) != len(result_ids)
            or not set(result_ids) <= pool
        ):
            raise ValueError(
                "Calibration results contain duplicate or ineligible entities"
            )
        scores = [_number(row.get("score")) for row in results]
        if any(right > left + 1e-12 for left, right in itertools.pairwise(scores)):
            raise ValueError("Calibration response ranking is not ordered")
        profiles[identity].append(
            {
                "context": context,
                "group": case["group_id"],
                "positive": bool(expected),
                "top1": result_ids[0] in expected,
                "top5": bool(set(result_ids[:5]) & expected),
                "confusable": bool(confusable),
                "wrong": result_ids[0] in confusable,
                "features": features(response),
            }
        )
    if seen != set(captured):
        raise ValueError(
            "Calibration responses must cover exactly the development cases"
        )
    if len(profiles) > MAX_PROFILES:
        raise ValueError("Too many calibration profiles")
    return dict(profiles)


def _grid(values: list[int], name: str) -> list[int]:
    lower, _ = _bounds(name)
    result = sorted({lower, *values, *(value + 1 for value in values)})
    if len(result) > 64:
        result = [result[index * (len(result) - 1) // 63] for index in range(64)]
    return result


def _wilson_upper(successes: int, count: int) -> float:
    z = 1.959963984540054
    proportion = successes / count
    return (
        proportion
        + z * z / (2 * count)
        + z
        * math.sqrt(proportion * (1 - proportion) / count + z * z / (4 * count * count))
    ) / (1 + z * z / count)


def _fit_profile(rows: list[dict], targets: dict) -> dict:
    context = rows[0]["context"]
    groups: dict[str, list[int]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[row["group"]].append(index)
    positive: list[tuple[int, bool, bool]] = []
    negative: list[int] = []
    confusable: list[int] = []
    for indexes in groups.values():
        mask = sum(1 << index for index in indexes)
        labels = {rows[index]["positive"] for index in indexes}
        if len(labels) != 1:
            raise ValueError(
                "A calibration group cannot mix positive and negative labels"
            )
        if True in labels:
            positive.append(
                (
                    mask,
                    all(rows[index]["top1"] for index in indexes),
                    all(rows[index]["top5"] for index in indexes),
                )
            )
        else:
            negative.append(mask)
        if any(rows[index]["confusable"] for index in indexes):
            confusable.append(
                sum(1 << index for index in indexes if rows[index]["wrong"])
            )
    if not positive or not negative:
        raise ValueError(
            "Every calibration profile requires positive and negative groups"
        )
    negative_limit = targets["no_match_per_modality"]
    if (
        _wilson_upper(0, len(negative))
        > negative_limit["false_acceptance_wilson95_upper_max"]
    ):
        raise ValueError(
            "Too few independent negative development groups for frozen acceptance constraints"
        )
    names = _FEATURES[context["query_type"]]
    choices: list[list[tuple[int, int]]] = []
    for name in names:
        values = [row["features"][name] for row in rows if row["features"] is not None]
        choices.append(
            [
                (
                    threshold,
                    sum(
                        1 << index
                        for index, row in enumerate(rows)
                        if row["features"] is not None
                        and row["features"][name] >= threshold
                    ),
                )
                for threshold in _grid(values, name)
            ]
        )
    best: tuple[int, ...] | None = None
    for candidate in itertools.product(*choices):
        accepted = (1 << len(rows)) - 1
        for _, mask in candidate:
            accepted &= mask
        top1 = sum(
            bool(correct and accepted & mask == mask) for mask, correct, _ in positive
        )
        if not top1:
            continue
        false = sum(bool(accepted & mask) for mask in negative)
        if (
            false / len(negative) > negative_limit["false_acceptance_max"]
            or _wilson_upper(false, len(negative))
            > negative_limit["false_acceptance_wilson95_upper_max"]
        ):
            continue
        wrong = sum(bool(accepted & mask) for mask in confusable)
        if (
            confusable
            and wrong / len(confusable)
            > targets["confusable_variant"]["wrong_variant_top1_max"]
        ):
            continue
        top5 = sum(
            bool(correct and accepted & mask == mask) for mask, _, correct in positive
        )
        objective = (
            top1,
            top5,
            -false,
            -wrong,
            *(threshold for threshold, _ in candidate),
        )
        if best is None or objective > best:
            best = objective
    if best is None:
        raise ValueError(
            "No useful calibration rule satisfies the frozen development constraints"
        )
    return {**context, "minimums": dict(zip(names, best[4:], strict=True))}


def fit(
    manifest: dict,
    fixture_bytes: bytes,
    responses_bytes: bytes,
    *,
    binary_sha256: str,
    contract_bytes: bytes,
) -> dict:
    """Fit frozen reviewed development captures; never reads evaluation cases or runs inference."""
    if (
        manifest.get("format_version") != 4
        or "calibration" in manifest
        or MEMBER in manifest["files"]
    ):
        raise ValueError("Calibration fitting requires an uncalibrated format4 bundle")
    if not _hash(binary_sha256) or digest(contract_bytes) != CONTRACT_SHA256:
        raise ValueError(
            "Calibration requires an executable hash and the frozen M1 contract"
        )
    fixture = _decode(fixture_bytes)
    responses = _decode(responses_bytes)
    profiles = _rows(manifest, fixture, responses, digest(fixture_bytes), binary_sha256)
    targets = _decode(contract_bytes)["quality_targets"]
    artifact = {
        "schema_version": 1,
        "feature_version": FEATURE_VERSION,
        "fit_version": FIT_VERSION,
        "protocol_sha256": PROTOCOL_SHA256,
        "retrieval_sha256": retrieval_identity(manifest),
        "development": {
            "fixture_sha256": digest(fixture_bytes),
            "responses_sha256": digest(responses_bytes),
            "binary_sha256": binary_sha256,
            "contract_sha256": digest(contract_bytes),
        },
        "profiles": [
            _fit_profile(profiles[identity], targets) for identity in sorted(profiles)
        ],
    }
    validate_artifact(artifact, manifest)
    return artifact


def verify_fit(
    artifact: dict,
    manifest: dict,
    fixture_bytes: bytes,
    responses_bytes: bytes,
    *,
    binary_sha256: str,
    contract_bytes: bytes,
) -> None:
    """Reproduce every threshold from its immutable development inputs."""
    validate_artifact(artifact, manifest)
    expected = fit(
        manifest,
        fixture_bytes,
        responses_bytes,
        binary_sha256=binary_sha256,
        contract_bytes=contract_bytes,
    )
    if canonical(expected) != canonical(artifact):
        raise ValueError(
            "Calibration artifact does not reproduce from its development proof"
        )
