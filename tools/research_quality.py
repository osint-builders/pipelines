"""Validate the research release without claiming calibrated identification."""

import json
import zipfile
from collections import Counter
from pathlib import Path

from build_cli import verify_bundle
from measure_release import RESEARCH_CONTRACT
from quality_gates import (
    ROOT,
    checked_artifact,
    metrics,
    mode,
    replay_research,
    sha256,
)
from quality_gates import (
    evaluate as identification_evaluate,
)

PROFILE = "research-search-v1"
FROZEN_FIXTURE = "d64d70d621445202f501adcea42a5984fda9d5d7928bead76dc133b3265bd81c"


def text_summary(report: dict, fixture: list[dict]) -> dict:
    rows = report.get("results", [])
    if len(rows) != len(fixture) or report.get("skipped"):
        raise ValueError("Every baseline text query must be reported without skips")
    hits = required = 0
    reciprocal = 0.0
    for case, row in zip(fixture, rows, strict=True):
        if any(row.get(key) != value for key, value in case.items()):
            raise ValueError("Text regression fixture changed")
        rank = row.get("rank")
        if rank is not None and (type(rank) is not int or rank < 1):
            raise ValueError("Invalid text result rank")
        hit = rank is not None and rank <= case["max_rank"]
        required += bool(case.get("required", True) and hit)
        hits += bool(rank is not None and rank <= 5)
        reciprocal += 1 / rank if rank else 0
    return {
        "cases": len(rows),
        "required_passed": required,
        "recall_at_5_hits": hits,
        "mrr": reciprocal / len(rows),
    }


def validate_text(report: dict, baseline: dict, dataset_id: str) -> dict:
    fixture_path = ROOT / "tests/fixtures/retrieval.json"
    fixture = json.loads(fixture_path.read_bytes())
    contract = json.loads((ROOT / "tests/fixtures/search_acceptance.json").read_bytes())
    if (
        report.get("dataset_id") != dataset_id
        or baseline["cases"]["sha256"] != sha256(fixture_path)
        or baseline["evaluation"]["dataset_id"] != contract["baseline"]["dataset_id"]
    ):
        raise ValueError("Text regression identity mismatch")
    current = text_summary(report, fixture)
    previous = text_summary(baseline["evaluation"], fixture)
    if (
        current["required_passed"] != sum(c.get("required", True) for c in fixture)
        or current["recall_at_5_hits"] < previous["recall_at_5_hits"]
        or current["mrr"] < previous["mrr"]
    ):
        raise ValueError("Research release text regression failed")
    return {"current": current, "baseline": previous}


def ranking_summary(fixture: dict, evaluation: dict) -> dict:
    cases = {row["id"]: row for row in fixture["cases"]}
    media = {row["id"]: row for row in fixture["media"]}
    rows = []
    for capture in evaluation["cases"]:
        case = cases[capture["id"]]
        response = capture["response"]
        ids = [row["id"] for row in response["results"]]
        if mode(case) != "text" and (
            response.get("calibration_status") != "uncalibrated"
            or response["match_status"] != "no_supported_match"
        ):
            raise ValueError(
                "Research image suggestions must not assert identification"
            )
        picture = media.get(case["query"].get("image_id"), {})
        rows.append(
            {
                **case,
                "scope": capture["scope"],
                "mode": mode(case),
                "rank": next(
                    (
                        i + 1
                        for i, value in enumerate(ids)
                        if value in case["expected_ids"]
                    ),
                    None,
                ),
                "first": ids[0] if ids else None,
                "photo_group": picture.get("photo_group"),
                "accepted": response["match_status"] == "candidates",
            }
        )
    summary: dict = {}
    for scope in ("global", "source_filtered"):
        scoped = [row for row in rows if row["scope"] == scope]
        groups = {
            "mode:" + key: [r for r in scoped if r["mode"] == key]
            for key in sorted({r["mode"] for r in scoped})
        }
        groups.update(
            {
                "task:" + key: [r for r in scoped if r["task"] == key]
                for key in sorted({r["task"] for r in scoped})
            }
        )
        summary[scope] = {}
        for key, group in groups.items():
            raw = metrics([{**row, "accepted": True} for row in group])
            # A ranking always produces suggestions; it has no acceptance decision.
            raw.pop("false_acceptance")
            summary[scope][key] = {"raw_ranking": raw, "accepted": metrics(group)}
    return summary


def evaluate(evidence: dict) -> dict:
    paths = {name: checked_artifact(ref) for name, ref in evidence.items()}
    required = {
        "binary",
        "bundle",
        "rebuilt_bundle",
        "contract",
        "research",
        "regression",
        "baseline",
        "benchmark_binary",
        "benchmark_bundle",
        "benchmark_contract",
        "benchmark_fixture",
        "benchmark_evaluation",
        "benchmark_research",
        "benchmark_query_media",
    }
    if not required <= paths.keys():
        raise ValueError("Research release evidence is incomplete")
    if paths["contract"].read_bytes() != RESEARCH_CONTRACT.read_bytes():
        raise ValueError("Research release contract changed")
    if sha256(paths["benchmark_fixture"]) != FROZEN_FIXTURE:
        raise ValueError(
            "Research release requires the original frozen held-out fixture"
        )
    manifest = verify_bundle(paths["bundle"])
    if (
        manifest.get("format_version") != 4
        or "calibration" in manifest
        or not manifest.get("observations")
        or not manifest.get("image")
    ):
        raise ValueError(
            "Research release requires embedded image and observation indices"
        )
    if sha256(paths["rebuilt_bundle"]) != sha256(paths["bundle"]):
        raise ValueError("Cached offline rebuild differs from release bundle")
    original = identification_evaluate(
        {
            name.removeprefix("benchmark_"): ref
            for name, ref in evidence.items()
            if name.startswith("benchmark_")
        }
    )
    checks = {row["name"]: row for row in original["checks"]}
    for key in (
        "frozen_evaluation_identity",
        "photo_group_separation",
        "release_sample_minimums",
        "integrity",
    ):
        if not checks[key]["passed"]:
            raise ValueError(
                f"Independent benchmark integrity failed: {key}: {checks[key]}"
            )
    with zipfile.ZipFile(paths["benchmark_bundle"]) as archive:
        benchmark = json.loads(archive.read("manifest.json"))
    if (
        manifest.get("text_vectors") != benchmark.get("text_vectors")
        or manifest["files"]["image/model.json"]
        != benchmark["files"]["image/model.json"]
    ):
        raise ValueError("Production encoder or vector storage differs from evaluation")
    if any(manifest[key] != benchmark[key] for key in ("model", "search")):
        # Captions count grows with the production corpus; the retrieval policy does not.
        if manifest["model"] != benchmark["model"] or {
            k: v for k, v in manifest["search"].items() if k != "captions"
        } != {k: v for k, v in benchmark["search"].items() if k != "captions"}:
            raise ValueError("Production retrieval differs from the evaluated policy")
    for key in ("model_sha256", "dimensions", "vector_dtype", "search"):
        if manifest["image"][key] != benchmark["image"][key]:
            raise ValueError(
                "Production image retrieval differs from the evaluated policy"
            )
    with zipfile.ZipFile(paths["bundle"]) as archive:
        images = json.loads(archive.read("image/index.json"))
        entities = json.loads(archive.read("index.json"))
    coverage = {}
    for source in manifest["sources"]:
        source_images = [row for row in images if row["source"] == source]
        indexed = [row for row in source_images if row["vector_index"] is not None]
        coverage[source] = {
            "entities": sum(e["source"] == source for e in entities),
            "saved_image_urls": len(source_images),
            "indexed_image_urls": len(indexed),
            "indexed_entities": len(
                {ref["entity_id"] for row in indexed for ref in row["references"]}
            ),
            "outcomes": dict(
                sorted(
                    Counter(
                        "indexed"
                        if row["vector_index"] is not None
                        else row["exclusion_reason"]
                        for row in source_images
                    ).items()
                )
            ),
        }
    research = replay_research(
        json.loads(paths["research"].read_bytes()), paths["binary"], paths["bundle"]
    )
    text = validate_text(
        json.loads(paths["regression"].read_bytes()),
        json.loads(paths["baseline"].read_bytes()),
        manifest["dataset_id"],
    )
    fixture = json.loads(paths["benchmark_fixture"].read_bytes())
    captures = json.loads(paths["benchmark_evaluation"].read_bytes())
    return {
        "schema_version": 1,
        "profile": PROFILE,
        "research_release_ready": True,
        "dataset_id": manifest["dataset_id"],
        "bundle_sha256": sha256(paths["bundle"]),
        "binary_sha256": sha256(paths["binary"]),
        "contract_sha256": sha256(paths["contract"]),
        "evidence": evidence,
        "coverage": coverage,
        "text_regression": text,
        "research_commands_verified": research,
        "benchmark_scope": "Frozen query-disjoint 4337-entity gallery; production corpus is larger. Raw similarity is not verified identity.",
        "independent_rankings": ranking_summary(fixture, captures),
        "original_identification_contract": {
            "passed": original["release_quality_established"],
            "checks": original["checks"],
        },
    }


def validate_report(report: dict, replacements: dict[str, Path]) -> None:
    references = {name: dict(ref) for name, ref in report["evidence"].items()}
    for name, path in replacements.items():
        if name not in references:
            raise ValueError("Unknown research evidence replacement")
        references[name]["path"] = str(path.resolve())
    actual = evaluate(references)
    actual["evidence"] = report["evidence"]
    if actual != report:
        raise ValueError("Research report differs from its recomputed evidence")
