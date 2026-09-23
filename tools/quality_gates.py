"""Recompute release quality checks from hashed fixtures and captured CLI responses."""

import argparse
import hashlib
import json
import math
import subprocess
import zipfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from accept_cli import (
    accept_research,
    bundle_calibration,
    bundle_evidence,
    calibration_eligible_ids,
    validate_calibration_response,
    validate_image_response,
    validate_observation_response,
)
from benchmark_images import proportion, validate_fixture
from benchmark_ranking import validate_response as validate_text_response

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_RELEASE_CHECKS = (
    "frozen_evaluation_identity",
    "photo_group_separation",
    "release_sample_minimums",
    "exact_designation",
    "descriptive",
    "specification",
    "photograph",
    "image_text",
    "visual_slices",
    "confusable_variant",
    "no_match_per_modality",
    "text_regression",
    "combined_regression",
    "integrity",
    "calibration",
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def artifact(path: Path) -> dict:
    return {"path": str(path.resolve()), "sha256": sha256(path)}


def checked_artifact(reference: dict) -> Path:
    path = Path(reference["path"])
    if not path.is_file() or sha256(path) != reference["sha256"]:
        raise ValueError(f"Quality evidence checksum mismatch: {path}")
    return path


def mode(case: dict) -> str:
    query = case["query"]
    return (
        "image_text"
        if query.get("image_id") and query.get("text")
        else "image"
        if query.get("image_id")
        else "text"
    )


def metrics(rows: list[dict]) -> dict:
    positive = [row for row in rows if row["expected_ids"]]
    negative = [row for row in rows if not row["expected_ids"]]
    confusable = [row for row in positive if row.get("confusable_ids")]
    return {
        "query_failures": sum(bool(row.get("failed")) for row in rows),
        "positive_cases": len(positive),
        "positive_photo_groups": len(
            {row["photo_group"] for row in positive if row.get("photo_group")}
        ),
        "positive_entities": len(
            {entity for row in positive for entity in row["expected_ids"]}
        ),
        "top1": proportion(
            sum(row["accepted"] and row["rank"] == 1 for row in positive),
            len(positive),
        ),
        "recall_at_5": proportion(
            sum(
                row["accepted"] and 0 < (row["rank"] or math.inf) <= 5
                for row in positive
            ),
            len(positive),
        ),
        "confusable_cases": len(confusable),
        "wrong_variant_top1": proportion(
            sum(
                row["accepted"] and row["first"] in row["confusable_ids"]
                for row in confusable
            ),
            len(confusable),
        ),
        "negative_cases": len(negative),
        "negative_photo_groups": len(
            {row["photo_group"] for row in negative if row.get("photo_group")}
        ),
        "in_domain_negatives": sum(
            row.get("negative_domain") == "in_domain" for row in negative
        ),
        "false_acceptance": proportion(
            sum(row["accepted"] for row in negative), len(negative)
        ),
    }


def passing(metrics: dict, targets: dict) -> bool:
    for name, threshold in targets.items():
        if name.endswith("_min"):
            value = metrics[name.removesuffix("_min")]["rate"]
            if value is None or value < threshold:
                return False
        elif name.endswith("_wilson95_upper_max"):
            value = metrics[name.removesuffix("_wilson95_upper_max")]["wilson95"]
            if value is None or value[1] > threshold:
                return False
        elif name.endswith("_max"):
            value = metrics[name.removesuffix("_max")]["rate"]
            if value is None or value > threshold:
                return False
        else:
            raise ValueError(f"Unknown quality target: {name}")
    return True


def reviewed_labels(fixture: dict, media: dict, bundle: Path) -> bool:
    """Require concrete review rationales and quotes in the captured evidence."""
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for case in fixture["cases"]:
            if case["status"] != "ready" or case["split"] == "gallery":
                continue
            picture = media.get(case["query"].get("image_id"), {})
            review = picture.get("review", case.get("review", {}))
            if (
                not review.get("reviewer", "").strip()
                or not review.get("rationale", "").strip()
            ):
                return False
            supported: set[str] = set()
            for reference in review.get("evidence", []):
                name = "entities/" + reference["entity_id"].replace(":", "/") + ".json"
                raw = archive.read(name)
                if hashlib.sha256(raw).hexdigest() != manifest["files"].get(name):
                    return False
                entity = json.loads(raw)
                page = next(
                    (
                        page
                        for page in entity["evidence"]
                        if page["id"] == reference["evidence_id"]
                    ),
                    None,
                )
                quote = reference.get("quote", "")
                if (
                    page is None
                    or not quote
                    or not any(
                        quote in page.get(field, "")
                        for field in ("markdown", "search_text", "html")
                    )
                ):
                    return False
                supported.add(reference["entity_id"])
            if case["expected_ids"] and not set(case["expected_ids"]) <= supported:
                return False
            if not case["expected_ids"] and case.get("negative_domain") not in {
                "in_domain",
                "unrelated",
            }:
                return False
    return True


def text_regression(
    report: dict, baseline: dict, fixture: list[dict], identity: dict
) -> tuple[bool, dict]:
    current = report.get("evaluation", {})
    previous = baseline.get("evaluation", {})
    bound = (
        report.get("binary", {}).get("sha256") == identity["binary_sha256"]
        and report.get("cases", {}).get("sha256") == identity["fixture_sha256"]
        and baseline.get("cases", {}).get("sha256") == identity["fixture_sha256"]
        and current.get("dataset_id") == identity["dataset_id"]
        and report.get("dataset", {}).get("content_sha256")
        == identity["content_sha256"]
        and baseline.get("dataset", {}).get("content_sha256")
        == identity["content_sha256"]
        and previous.get("dataset_id") == identity["baseline_dataset_id"]
    )
    summaries = {}
    for name, value in (("current", current), ("baseline", previous)):
        rows = value.get("results", [])
        if len(rows) != len(fixture) or value.get("skipped"):
            return False, {
                "reason": "All frozen baseline cases must be evaluated without skips"
            }
        required = hits = 0
        reciprocal = 0.0
        for case, row in zip(fixture, rows, strict=True):
            if any(row.get(key) != expected for key, expected in case.items()):
                return False, {"reason": "Regression query labels/order changed"}
            rank = row.get("rank")
            if rank is not None and (type(rank) is not int or rank < 1):
                return False, {"reason": "Invalid result rank"}
            if case.get("required", True):
                required += bool(rank is not None and rank <= case["max_rank"])
            hits += bool(rank is not None and rank <= 5)
            reciprocal += 1 / rank if rank else 0
        summaries[name] = {
            "required_passed": required,
            "recall_at_5_hits": hits,
            "mrr": reciprocal / len(fixture),
        }
    expected_required = sum(case.get("required", True) for case in fixture)
    passed = (
        bound
        and summaries["current"]["required_passed"] == expected_required
        and summaries["current"]["recall_at_5_hits"]
        >= summaries["baseline"]["recall_at_5_hits"]
        and summaries["current"]["mrr"] >= summaries["baseline"]["mrr"]
    )
    return passed, {
        "identity_matches": bound,
        "cases": len(fixture),
        "required_cases": expected_required,
        **summaries,
    }


def capture_research(binary: Path, bundle: Path, output: Path) -> None:
    commands = []

    def run(*args: str) -> bytes:
        body = subprocess.run(
            [str(binary.resolve()), *args], capture_output=True, check=True, timeout=120
        ).stdout
        commands.append({"args": list(args), "response": json.loads(body)})
        return body

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if "research" not in manifest:
            raise ValueError(
                "Research acceptance requires an embedded research dataset"
            )
        accept_research(run, archive, manifest, json.loads(run("info")))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "protocol_sha256": sha256(ROOT / "tools/accept_cli.py"),
                "binary_sha256": sha256(binary),
                "bundle_sha256": sha256(bundle),
                "dataset_id": manifest["dataset_id"],
                "commands": commands,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def replay_research(proof: dict, binary: Path, bundle: Path) -> int:
    if (
        proof.get("schema_version") != 1
        or proof.get("protocol_sha256") != sha256(ROOT / "tools/accept_cli.py")
        or proof.get("binary_sha256") != sha256(binary)
        or proof.get("bundle_sha256") != sha256(bundle)
    ):
        raise ValueError("Research acceptance identity mismatch")
    position = 0

    def run(*args: str) -> bytes:
        nonlocal position
        if position >= len(proof["commands"]):
            raise ValueError("Research acceptance is incomplete")
        command = proof["commands"][position]
        position += 1
        if command["args"] != list(args):
            raise ValueError("Research acceptance command sequence mismatch")
        return json.dumps(command["response"]).encode()

    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if (
            "research" not in manifest
            or proof.get("dataset_id") != manifest["dataset_id"]
        ):
            raise ValueError("Research acceptance dataset mismatch")
        accept_research(run, archive, manifest, json.loads(run("info")))
    if position != len(proof["commands"]):
        raise ValueError("Unexpected research acceptance commands")
    return position


def query_failure(row: dict, case: dict, picture: dict, dataset_id: str) -> None:
    failure = row["failure"]
    if (
        "response" in row
        or not picture
        or failure.get("exit_code") != 1
        or failure.get("stdout") != ""
        or failure.get("dataset_id") != dataset_id
        or failure.get("query") != case["query"].get("text", "")
        or failure.get("query_image_sha256") != picture["sha256"]
        or json.loads(failure["stderr"]) != {"error": "invalid or truncated image"}
    ):
        raise ValueError("Invalid captured image rejection")


def response_integrity(
    evaluation: dict,
    fixture: dict,
    bundle: Path,
    *,
    allow_image_rejections: bool = False,
) -> int:
    cases = {case["id"]: case for case in fixture["cases"]}
    pictures = {picture["id"]: picture for picture in fixture["media"]}
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))

        def checked(name: str) -> Any:
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != manifest["files"].get(name):
                raise ValueError("Response integrity bundle checksum mismatch")
            return json.loads(raw)

        entities = {row["id"]: row for row in checked("index.json")}
        evidence = bundle_evidence(archive)
        images = (
            {row["id"]: row for row in checked("image/index.json")}
            if "image" in manifest
            else {}
        )
        observations = (
            {row["id"]: row for row in checked("observations/index.json")}
            if "observations" in manifest
            else {}
        )
        recipes = (
            checked("observations/recipes.json") if "observations" in manifest else {}
        )
        calibration = bundle_calibration(archive, manifest)
        count = 0
        for row in evaluation["cases"]:
            case = cases[row["id"]]
            if "failure" in row:
                query_failure(
                    row,
                    case,
                    pictures.get(case["query"].get("image_id"), {}),
                    manifest["dataset_id"],
                )
                if not allow_image_rejections:
                    raise ValueError("Evaluation includes rejected query images")
                continue
            response = row["response"]
            source = (
                case["query"].get("source", "")
                if row["scope"] == "source_filtered"
                else ""
            )
            picture = pictures.get(case["query"].get("image_id"))
            eligible_ids = [
                key
                for key, entity in entities.items()
                if not source or entity["source"] == source
            ]
            eligible_ids = calibration_eligible_ids(mode(case), eligible_ids, images)
            if response.get("observations"):
                validate_observation_response(
                    response,
                    manifest,
                    observations,
                    recipes,
                    evidence,
                    enabled=True,
                    source=source,
                    image_sha256=picture["sha256"] if picture else None,
                    images=images,
                    calibration=calibration,
                    eligible_ids=eligible_ids,
                )
            elif picture:
                validate_image_response(
                    response,
                    manifest,
                    picture["sha256"],
                    mode(case) == "image_text",
                    source=source,
                    limit=20,
                    calibration=calibration,
                    eligible_ids=eligible_ids,
                    images=images,
                )
            else:
                validate_calibration_response(
                    response,
                    manifest,
                    calibration=calibration,
                    eligible_ids=eligible_ids,
                )
                validate_text_response(response, entities, archive, manifest, source)
            for result in response["results"]:
                if (
                    result["id"] not in entities
                    or result["source"] != entities[result["id"]]["source"]
                ):
                    raise ValueError("Result entity identity mismatch")
                pages = evidence[result["id"]]
                # Entity-name snippets have no page ID; their contributions resolve it.
                if (
                    not isinstance(result.get("evidence_id"), str)
                    or result["evidence_id"]
                    and result["evidence_id"] not in pages
                    or not result["matches"]
                ):
                    raise ValueError("Result evidence is unrelated to its entity")
                for match in result["matches"]:
                    if pages.get(match.get("evidence_id")) != match.get("url"):
                        raise ValueError("Result contribution has unrelated evidence")
                    if media_id := match.get("media_id"):
                        record = images.get(media_id)
                        if record is None or not any(
                            ref["entity_id"] == result["id"]
                            and ref["evidence_id"] == match["evidence_id"]
                            for ref in record["references"]
                        ):
                            raise ValueError("Result contribution has unrelated media")
                        if match["channel"] == "image" and (
                            record["vector_index"] is None
                            or match.get("model_sha256")
                            != manifest["image"]["model_sha256"]
                        ):
                            raise ValueError(
                                "Result image has no matching model/vector"
                            )
                count += 1
        return count


def calibration_separation(development: dict, fixture: dict) -> dict:
    """Keep development inputs separate from held-out queries and frozen pilots."""
    media = {
        row["sha256"]: row
        for row in fixture.get("media", [])
        if row.get("status") == "captured"
    }
    held = [case for case in fixture["cases"] if case["split"] == "evaluation"]
    protected_groups = {
        row["photo_group"] for row in media.values() if row["split"] != "development"
    }
    protected_groups.update(
        case.get("entity_group", case.get("group_id")) for case in held
    )
    protected_hashes = {
        row["sha256"] for row in media.values() if row["split"] != "development"
    }
    protected_ids = {
        key
        for case in held
        if mode(case) == "text"
        for key in case["expected_ids"] + case.get("confusable_ids", [])
    }
    protected_text = {
        " ".join(case["query"].get("text", "").casefold().split())
        for case in held
        if case["query"].get("text")
    }
    seed_hashes = {}
    for name in (
        "multimodal.json",
        "ranking.json",
        "ranking_combined.json",
        "retrieval.json",
    ):
        path = ROOT / "tests/fixtures" / name
        seed_hashes[name] = sha256(path)
        seed = json.loads(path.read_bytes())
        if isinstance(seed, list):
            protected_ids.update(case["expected"] for case in seed)
            protected_text.update(
                " ".join(case["query"].casefold().split()) for case in seed
            )
            continue
        if seed.get("status") == "development_frozen_before_retrieval":
            continue
        for picture in seed.get("media", []):
            if picture.get("split") == "evaluation":
                protected_groups.add(picture["photo_group"])
                if picture.get("sha256"):
                    protected_hashes.add(picture["sha256"])
        for case in seed["cases"]:
            if case["split"] != "evaluation":
                continue
            protected_groups.add(case.get("entity_group", case.get("group_id")))
            query = case["query"]
            text = query if isinstance(query, str) else query.get("text", "")
            if text:
                protected_text.add(" ".join(text.casefold().split()))
            if isinstance(query, str) or not query.get("image_id"):
                protected_ids.update(
                    case["expected_ids"] + case.get("confusable_ids", [])
                )
    checked_photos: set[str] = set()
    for case in development["cases"]:
        query = case["query"]
        if case["group_id"] in protected_groups:
            raise ValueError(
                "Calibration development photo group overlaps held-out/gallery inputs"
            )
        if query.get("image_sha256"):
            checksum = query["image_sha256"]
            picture = media.get(checksum)
            if (
                checksum in protected_hashes
                or picture is None
                or picture["split"] != "development"
                or picture["photo_group"] != case["group_id"]
            ):
                raise ValueError(
                    "Calibration image must have a separate registered development photo group"
                )
            checked_photos.add(checksum)
        else:
            if (
                set(case["expected_ids"] + case.get("confusable_ids", []))
                & protected_ids
            ):
                raise ValueError(
                    "Calibration development text entity overlaps held-out/frozen seed labels"
                )
        text = " ".join((query.get("text") or "").casefold().split())
        if not query.get("image_sha256") and text and text in protected_text:
            raise ValueError(
                "Calibration development query repeats a held-out/frozen seed query"
            )
    return {
        "development_photos": len(checked_photos),
        "frozen_seed_sha256": seed_hashes,
    }


def calibration_proof(paths: dict[str, Path], fixture: dict, evaluation: dict) -> dict:
    """Refit development evidence and replay every held-out acceptance decision."""
    from pipelines.calibration import (
        canonical,
        digest,
        retrieval_identity,
        scope_identity,
        verify_fit,
    )

    required = {
        "development_fixture",
        "development_responses",
        "development_binary",
        "development_bundle",
    }
    if not required <= paths.keys():
        raise ValueError(
            "Calibration proof requires hashed development fixture, responses, binary, and bundle"
        )
    with zipfile.ZipFile(paths["bundle"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        fitted = bundle_calibration(archive, manifest)
        if manifest.get("format_version") != 5 or fitted is None:
            raise ValueError(
                "Calibration proof requires an embedded fitted format-5 artifact"
            )
        raw_index = archive.read("index.json")
        if digest(raw_index) != manifest["files"].get("index.json"):
            raise ValueError("Calibration entity index checksum mismatch")
        entities = {row["id"]: row for row in json.loads(raw_index)}
        raw_images = archive.read("image/index.json")
        if digest(raw_images) != manifest["files"].get("image/index.json"):
            raise ValueError("Calibration image index checksum mismatch")
        images = {row["id"]: row for row in json.loads(raw_images)}
    with zipfile.ZipFile(paths["development_bundle"]) as archive:
        base = json.loads(archive.read("manifest.json"))
        if (
            base.get("format_version") != 4
            or base.get("calibration")
            or retrieval_identity(base) != retrieval_identity(manifest)
        ):
            raise ValueError(
                "Calibration development bundle retrieval identity differs from release"
            )
        for name, checksum in base["files"].items():
            if digest(archive.read(name)) != checksum:
                raise ValueError(
                    "Calibration development bundle member checksum mismatch"
                )
    fixture_bytes = paths["development_fixture"].read_bytes()
    responses_bytes = paths["development_responses"].read_bytes()
    verify_fit(
        fitted,
        base,
        fixture_bytes,
        responses_bytes,
        binary_sha256=sha256(paths["development_binary"]),
        contract_bytes=paths["contract"].read_bytes(),
    )
    development = json.loads(fixture_bytes)
    raw_responses = json.loads(responses_bytes)
    if raw_responses.get("dataset_id") != base["dataset_id"]:
        raise ValueError("Calibration development responses use a different dataset")
    binding = {
        "artifact_sha256": digest(canonical(fitted)),
        "development_fixture_sha256": digest(fixture_bytes),
        "development_responses_sha256": digest(responses_bytes),
    }
    if (
        fixture.get("status") != "frozen_before_retrieval"
        or fixture.get("calibration") != binding
        or evaluation.get("fixture_sha256") != sha256(paths["fixture"])
        or evaluation.get("calibration_sha256") != binding["artifact_sha256"]
    ):
        raise ValueError(
            "Held-out fixture/evaluation does not bind frozen calibration parameters"
        )
    separation = calibration_separation(development, fixture)
    labeled = {
        "cases": [
            {
                **case,
                "status": "ready",
                "query": {"text": case["query"].get("text", "")},
            }
            for case in development["cases"]
        ]
    }
    if not reviewed_labels(labeled, {}, paths["bundle"]):
        raise ValueError(
            "Calibration development labels require archived evidence and review rationale"
        )
    for ids in development["scopes"].values():
        if not set(ids) <= entities.keys():
            raise ValueError("Calibration scope includes absent entities")
    development_cases = {case["id"]: case for case in development["cases"]}
    for row in raw_responses["cases"]:
        case = development_cases[row["case_id"]]
        eligible = development["scopes"][case["scope"]]
        if set(eligible) != set(
            calibration_eligible_ids(case["query_type"], eligible, images)
        ):
            raise ValueError("Calibration image scope includes unindexed entities")
        if not {item["id"] for item in row["response"]["results"]} <= set(eligible):
            raise ValueError("Calibration development response leaks its eligible pool")
    raw_fixture = {
        "cases": [
            {
                **case,
                "query": {
                    "text": case["query"].get("text", ""),
                    "image_id": case["query"].get("image_sha256"),
                },
            }
            for case in development["cases"]
        ],
        "media": [
            {
                "id": picture["sha256"],
                **{key: value for key, value in picture.items() if key != "id"},
            }
            for picture in fixture.get("media", [])
            if picture.get("status") == "captured"
        ],
    }
    response_integrity(
        {
            "cases": [
                {"id": row["case_id"], "scope": "global", "response": row["response"]}
                for row in raw_responses["cases"]
            ]
        },
        raw_fixture,
        paths["development_bundle"],
    )
    cases = {
        case["id"]: case
        for case in fixture["cases"]
        if case["split"] == "evaluation" and case["status"] == "ready"
    }
    profiles: set[str] = set()
    for row in evaluation["cases"]:
        case = cases[row["id"]]
        source = (
            case["query"].get("source") if row["scope"] == "source_filtered" else None
        )
        eligible = calibration_eligible_ids(
            mode(case),
            [
                key
                for key, entity in entities.items()
                if not source or entity["source"] == source
            ],
            images,
        )
        response = row["response"]
        if not validate_calibration_response(
            response, manifest, calibration=fitted, eligible_ids=eligible, images=images
        ):
            raise ValueError(
                "Held-out evaluation has no fitted profile for its exact eligible pool"
            )
        if response["results"] and len(response["results"]) < min(2, len(eligible)):
            raise ValueError(
                "Calibration proof requires uncropped first-two response candidates"
            )
        if row.get("eligible_entities_sha256") != scope_identity(eligible):
            raise ValueError(
                "Held-out response eligible pool binding differs from its actual filters"
            )
        profiles.add(response["decision"]["profile_id"])
    if not evaluation["cases"]:
        raise ValueError("Calibration proof requires held-out decisions")
    return {
        **binding,
        **separation,
        "development_cases": len(development["cases"]),
        "held_out_decisions": len(evaluation["cases"]),
        "profiles_evaluated": len(profiles),
    }


def evaluate(evidence: dict) -> dict:
    """Read immutable artifacts and derive every check; omitted evidence fails closed."""
    paths = {name: checked_artifact(ref) for name, ref in evidence.items()}
    for name in ("binary", "bundle", "contract"):
        if name not in paths:
            raise ValueError(f"Missing required artifact: {name}")
    contract = json.loads(paths["contract"].read_bytes())
    if contract.get("status") != "frozen_after_m1_user_verification":
        raise ValueError("Quality checks require the frozen acceptance contract")
    if contract != json.loads(
        (ROOT / "tests/fixtures/search_acceptance.json").read_bytes()
    ):
        raise ValueError("Quality checks cannot replace the frozen acceptance targets")
    with zipfile.ZipFile(paths["bundle"]) as archive:
        manifest = json.loads(archive.read("manifest.json"))

        def member(name: str) -> Any:
            raw = archive.read(name)
            if hashlib.sha256(raw).hexdigest() != manifest["files"].get(name):
                raise ValueError(f"Quality bundle member checksum mismatch: {name}")
            return json.loads(raw)

        entities = {row["id"]: row for row in member("index.json")}
        gallery = member("image/index.json") if "image" in manifest else []
    checks = {
        name: {"name": name, "passed": False, "reason": "Evidence not supplied"}
        for name in REQUIRED_RELEASE_CHECKS
    }

    def check(name: str, passed: bool, detail: object) -> None:
        checks[name] = {"name": name, "passed": bool(passed), "detail": detail}

    rows: list[dict] = []
    fixture: dict = {}
    evaluation: dict = {}
    ready: list[dict] = []
    integrity_errors: list[str] = []
    if "fixture" in paths and "evaluation" in paths:
        fixture = json.loads(paths["fixture"].read_bytes())
        evaluation = json.loads(paths["evaluation"].read_bytes())
        try:
            media = validate_fixture(fixture)
            identity_ok = (
                fixture.get("status") == "frozen_before_retrieval"
                and fixture.get("source_content_sha256") == manifest["content_sha256"]
                and evaluation.get("schema_version") == 1
                and evaluation.get("split") == "evaluation"
                and evaluation.get("fixture_sha256") == sha256(paths["fixture"])
                and evaluation.get("binary_sha256") == sha256(paths["binary"])
                and evaluation.get("dataset_id") == manifest["dataset_id"]
                and evaluation.get("gallery_sha256")
                == manifest.get("image", {}).get("gallery_sha256")
            )
            check(
                "frozen_evaluation_identity",
                identity_ok,
                {
                    "fixture_sha256": sha256(paths["fixture"]),
                    "dataset_id": evaluation.get("dataset_id"),
                },
            )
            indexed = {
                row["sha256"] for row in gallery if row["vector_index"] is not None
            }
            allowed = {
                row["sha256"]
                for row in media.values()
                if row["split"] == "gallery" and row["status"] == "captured"
            }
            protected = {
                row["sha256"]
                for row in media.values()
                if row["split"] != "gallery" and row["status"] == "captured"
            }
            with (
                zipfile.ZipFile(paths["query_media"])
                if "query_media" in paths
                else nullcontext()
            ) as images:
                for picture in media.values():
                    if picture["status"] == "captured":
                        if images is not None:
                            image_hash = hashlib.sha256(
                                images.read(picture["sha256"])
                            ).hexdigest()
                        else:
                            local = Path(picture["local_path"])
                            if not local.is_absolute():
                                local = ROOT / local
                            image_hash = sha256(local)
                        if image_hash != picture["sha256"]:
                            raise ValueError("Query or gallery image bytes changed")
            labels_ok = reviewed_labels(fixture, media, paths["bundle"])
            check(
                "photo_group_separation",
                indexed <= allowed and not indexed & protected and labels_ok,
                {
                    "indexed_hashes": len(indexed),
                    "unregistered_gallery_hashes": len(indexed - allowed),
                    "query_hashes_in_gallery": len(indexed & protected),
                    "source_supported_review_labels": labels_ok,
                    "note": "Photo-group labels are reviewed evidence; hash checks additionally reject exact leakage and declared derivative/split overlap.",
                },
            )
            ready = [
                case
                for case in fixture["cases"]
                if case["split"] == "evaluation" and case["status"] == "ready"
            ]
            descriptors: set[tuple[str, str, str]] = set()
            for case in ready:
                text = " ".join(case["query"].get("text", "").casefold().split())
                picture = media.get(case["query"].get("image_id"), {})
                descriptor = (mode(case), picture.get("sha256", ""), text)
                if descriptor in descriptors:
                    raise ValueError(
                        "Duplicate query descriptors cannot enlarge the evaluation sample"
                    )
                descriptors.add(descriptor)
                source = case["query"].get("source")
                if case["expected_ids"] and (
                    not source
                    or not any(
                        entity.startswith(source + ":")
                        for entity in case["expected_ids"]
                    )
                ):
                    raise ValueError(
                        "Positive cases require an expected pilot source for scoped evaluation"
                    )
            expected = {
                (case["id"], scope)
                for case in ready
                for scope in ("global", "source_filtered")
                if scope == "global" or case["query"].get("source")
            }
            actual = [(row["id"], row["scope"]) for row in evaluation["cases"]]
            if len(actual) != len(set(actual)) or set(actual) != expected:
                raise ValueError(
                    "Evaluation must include every ready case once in each required scope"
                )
            by_id = {case["id"]: case for case in ready}
            for item in evaluation["cases"]:
                case = by_id[item["id"]]
                if "failure" in item:
                    picture = media.get(case["query"].get("image_id"), {})
                    query_failure(item, case, picture, manifest["dataset_id"])
                    if not set(case["expected_ids"]) <= entities.keys():
                        raise ValueError("Expected entity is absent from the bundle")
                    rows.append(
                        {
                            **case,
                            "scope": item["scope"],
                            "mode": mode(case),
                            "photo_group": picture.get("photo_group"),
                            "rank": None,
                            "first": None,
                            "accepted": False,
                            "failed": True,
                        }
                    )
                    continue
                response = item["response"]
                expected_text = case["query"].get("text", "")
                expected_image = (
                    media[case["query"]["image_id"]]["sha256"]
                    if case["query"].get("image_id")
                    else None
                )
                if (
                    response["dataset_id"] != manifest["dataset_id"]
                    or response["query_type"] != mode(case)
                    or response["match_status"]
                    not in {"candidates", "no_supported_match"}
                    or response.get("query", "") != expected_text
                    or response.get("query_image_sha256") != expected_image
                ):
                    raise ValueError(
                        "Response identity, query bytes/text, modality, or match status mismatch"
                    )
                results = response["results"]
                ids = [result["id"] for result in results]
                if len(ids) != len(set(ids)):
                    integrity_errors.append(case["id"] + ": duplicate entities")
                for entity_id in ids:
                    if entity_id not in entities or (
                        item["scope"] == "source_filtered"
                        and entities[entity_id]["source"] != case["query"]["source"]
                    ):
                        integrity_errors.append(
                            case["id"] + ": absent entity/filter leak"
                        )
                if not set(case["expected_ids"]) <= entities.keys():
                    raise ValueError("Expected entity is absent from the bundle")
                rows.append(
                    {
                        **case,
                        "scope": item["scope"],
                        "mode": mode(case),
                        "photo_group": media[case["query"]["image_id"]]["photo_group"]
                        if case["query"].get("image_id")
                        else None,
                        "rank": next(
                            (
                                i + 1
                                for i, entity_id in enumerate(ids)
                                if entity_id in case["expected_ids"]
                            ),
                            None,
                        ),
                        "first": ids[0] if ids else None,
                        "accepted": response["match_status"] == "candidates",
                    }
                )
        except (KeyError, ValueError, OSError, TypeError) as error:
            rows = []
            check("frozen_evaluation_identity", False, str(error))
    global_rows = [row for row in rows if row["scope"] == "global"]
    targets = contract["quality_targets"]
    minimums = contract["evaluation"]["release_minimums"]
    by_mode = {
        name: metrics([row for row in global_rows if row["mode"] == name])
        for name in ("text", "image", "image_text")
    }
    by_task = {
        name: metrics([row for row in global_rows if row["task"] == name])
        for name in (
            "exact_designation",
            "descriptive",
            "specification",
            "unseen_view",
            "crop",
            "diagram",
        )
    }
    for task in ("exact_designation", "descriptive", "specification"):
        check(task, passing(by_task[task], targets[task]), by_task[task])
    for name, task in (("image", "photograph"), ("image_text", "image_text")):
        check(task, passing(by_mode[name], targets[task]), by_mode[name])
    slices = {name: by_task[name] for name in ("unseen_view", "crop", "diagram")}
    check(
        "visual_slices",
        all(
            passing(value, targets["unseen_view_crop_diagram_each"])
            for value in slices.values()
        ),
        slices,
    )
    confusable = metrics([row for row in global_rows if row.get("confusable_ids")])
    check(
        "confusable_variant",
        passing(confusable, targets["confusable_variant"]),
        confusable,
    )
    check(
        "no_match_per_modality",
        all(
            passing(value, targets["no_match_per_modality"])
            for value in by_mode.values()
        ),
        by_mode,
    )
    counts_ok = all(
        by_task[name]["positive_cases"] >= minimums["text_queries_per_task"]
        for name in ("exact_designation", "descriptive", "specification")
    )
    counts_ok &= all(
        value["positive_photo_groups"]
        >= minimums["independent_positive_photo_groups_per_image_modality"]
        and value["positive_entities"]
        >= minimums["distinct_positive_entities_per_image_modality"]
        for name, value in by_mode.items()
        if name != "text"
    )
    counts_ok &= all(
        value["negative_cases"] >= minimums["negative_queries_per_modality"]
        and value["in_domain_negatives"]
        >= minimums["negative_in_domain_fraction"] * value["negative_cases"]
        for value in by_mode.values()
    )
    counts_ok &= all(
        by_mode[name]["negative_photo_groups"]
        >= minimums["negative_queries_per_modality"]
        for name in ("image", "image_text")
    )
    counts_ok &= confusable["confusable_cases"] >= minimums["confusable_queries"]
    counts_ok &= all(
        value["positive_cases"]
        >= minimums["queries_per_unseen_view_crop_diagram_slice"]
        for value in slices.values()
    )
    by_source = {
        source: metrics(
            [
                row
                for row in global_rows
                if any(
                    entity.startswith(source + ":") for entity in row["expected_ids"]
                )
            ]
        )
        for source in ("militaryperiscope", "commons")
    }
    counts_ok &= all(
        value["positive_cases"] >= minimums["positive_queries_per_pilot_source"]
        for value in by_source.values()
    )
    check(
        "release_sample_minimums",
        counts_ok,
        {
            "by_mode": by_mode,
            "by_task": by_task,
            "pilot_sources": by_source,
            "required": minimums,
            "pending": [
                case["id"]
                for case in fixture.get("cases", [])
                if case.get("status") != "ready"
            ],
        },
    )
    paired = []
    for combined in [
        row
        for row in global_rows
        if row["mode"] == "image_text" and row["expected_ids"]
    ]:
        matches = [
            row
            for row in global_rows
            if row["mode"] == "image"
            and row["query"]["image_id"] == combined["query"]["image_id"]
            and row["expected_ids"] == combined["expected_ids"]
        ]
        if len(matches) == 1:
            paired.append((matches[0], combined))
    image_hits = sum(
        row[0]["accepted"] and 0 < (row[0]["rank"] or math.inf) <= 5 for row in paired
    )
    combined_hits = sum(
        row[1]["accepted"] and 0 < (row[1]["rank"] or math.inf) <= 5 for row in paired
    )
    check(
        "combined_regression",
        bool(paired)
        and len(paired) == by_mode["image_text"]["positive_cases"]
        and combined_hits >= image_hits,
        {
            "paired_cases": len(paired),
            "image_hits": image_hits,
            "combined_hits": combined_hits,
        },
    )
    # Independent acceptance/regression/calibration artifacts have explicit protocols;
    # unsupported or absent protocols remain failures instead of trusting passed flags.
    check(
        "integrity",
        False,
        {
            "response_errors": integrity_errors,
            "reason": "Complete source/media reference and research acceptance proof required",
        },
    )
    if "research" in paths and rows and not integrity_errors:
        try:
            results_checked = response_integrity(evaluation, fixture, paths["bundle"])
            commands_checked = replay_research(
                json.loads(paths["research"].read_bytes()),
                paths["binary"],
                paths["bundle"],
            )
            check(
                "integrity",
                True,
                {
                    "results_checked": results_checked,
                    "research_commands_replayed": commands_checked,
                },
            )
        except (AssertionError, KeyError, ValueError, OSError, TypeError) as error:
            check("integrity", False, {"error": str(error) or type(error).__name__})
    check("text_regression", False, "Bound 116-case M1 regression proof required")
    if "regression" in paths and "baseline" in paths:
        fixture_path = ROOT / contract["baseline"]["text_cases"]
        passed, detail = text_regression(
            json.loads(paths["regression"].read_bytes()),
            json.loads(paths["baseline"].read_bytes()),
            json.loads(fixture_path.read_bytes()),
            {
                "binary_sha256": sha256(paths["binary"]),
                "fixture_sha256": sha256(fixture_path),
                "dataset_id": manifest["dataset_id"],
                "content_sha256": manifest["content_sha256"],
                "baseline_dataset_id": contract["baseline"]["dataset_id"],
            },
        )
        check("text_regression", passed, detail)
    check(
        "calibration",
        False,
        "Development-only fitted and frozen calibration proof required",
    )
    if (
        rows
        and checks["frozen_evaluation_identity"]["passed"]
        and checks["photo_group_separation"]["passed"]
    ):
        try:
            check("calibration", True, calibration_proof(paths, fixture, evaluation))
        except (AssertionError, KeyError, ValueError, OSError, TypeError) as error:
            check("calibration", False, {"error": str(error) or type(error).__name__})
    result = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "bundle_sha256": sha256(paths["bundle"]),
        "binary_sha256": sha256(paths["binary"]),
        "contract_sha256": sha256(paths["contract"]),
        "evidence": evidence,
        "checks": list(checks.values()),
        "release_quality_established": all(row["passed"] for row in checks.values()),
        "supporting_benchmarks": {
            name: {
                "release_evaluation": False,
                "scope": "Previously frozen pilot; does not replace the required expanded evaluation",
                "metrics": json.loads(path.read_bytes()).get("metrics"),
                "identity": json.loads(path.read_bytes()).get("identity"),
            }
            for name, path in paths.items()
            if name.startswith("pilot_")
        },
    }
    return result


def validate_report(report: dict, replacements: dict[str, Path] | None = None) -> None:
    """Reject stale, partial, edited, or unsupported release quality claims."""
    if report.get("schema_version") != 1 or not isinstance(
        report.get("evidence"), dict
    ):
        raise ValueError("Unsupported quality report")
    references = {name: dict(ref) for name, ref in report["evidence"].items()}
    for name, path in (replacements or {}).items():
        if name not in references:
            raise ValueError(f"Unknown quality evidence replacement: {name}")
        references[name]["path"] = str(path.resolve())
    actual = evaluate(references)
    actual["evidence"] = report["evidence"]
    if report != actual:
        raise ValueError("Quality report does not match its recomputed evidence")
    if not actual["release_quality_established"]:
        raise ValueError("Release quality gates are not satisfied")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--fixture", type=Path)
    parser.add_argument("--evaluation", type=Path)
    parser.add_argument("--regression", type=Path)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--query-media", type=Path)
    parser.add_argument("--research", type=Path)
    for name in (
        "development-fixture",
        "development-responses",
        "development-binary",
        "development-bundle",
    ):
        parser.add_argument("--" + name, type=Path)
    parser.add_argument("--capture-research", type=Path)
    parser.add_argument(
        "--contract", type=Path, default=ROOT / "tests/fixtures/search_acceptance.json"
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.capture_research:
        capture_research(args.binary, args.bundle, args.capture_research)
        args.research = args.capture_research
    evidence = {
        name: artifact(path)
        for name in (
            "binary",
            "bundle",
            "fixture",
            "evaluation",
            "contract",
            "regression",
            "baseline",
            "query_media",
            "research",
            "development_fixture",
            "development_responses",
            "development_binary",
            "development_bundle",
        )
        if (path := getattr(args, name)) is not None
    }
    report = evaluate(evidence)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "release_quality_established": report["release_quality_established"],
            }
        )
    )


if __name__ == "__main__":
    main()
