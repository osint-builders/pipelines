"""Compare two offline text CLIs using frozen source-backed ranking queries."""

import argparse
import hashlib
import json
import math
import subprocess
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from benchmark_images import ROOT, proportion


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_member(archive: zipfile.ZipFile, manifest: dict, name: str) -> bytes:
    raw = archive.read(name)
    if hashlib.sha256(raw).hexdigest() != manifest["files"].get(name):
        raise ValueError(f"Bundle member checksum mismatch: {name}")
    return raw


def validate_fixture(fixture: dict, archive: zipfile.ZipFile, manifest: dict) -> dict:
    index_raw = checked_member(archive, manifest, "index.json")
    if (
        fixture.get("schema_version") != 1
        or fixture.get("status") != "frozen_before_retrieval"
        or fixture.get("source_content_sha256") != manifest["content_sha256"]
        or fixture.get("source_entities_sha256")
        != hashlib.sha256(index_raw).hexdigest()
    ):
        raise ValueError("Fixture does not match the frozen source corpus")
    entities = {entity["id"]: entity for entity in json.loads(index_raw)}
    ids: set[str] = set()
    entity_splits: dict[str, str] = {}
    group_splits: dict[str, str] = {}
    splits: set[str] = set()
    for case in fixture["cases"]:
        split = case["split"]
        if case["id"] in ids or split not in {"development", "evaluation"}:
            raise ValueError("Duplicate query ID or invalid split")
        ids.add(case["id"])
        splits.add(split)
        if group_splits.setdefault(case["entity_group"], split) != split:
            raise ValueError("Named entity group crosses development/evaluation")
        if not case["query"].strip() or len(case["query"]) > 1000:
            raise ValueError("Invalid query text")
        if case["source"] not in manifest["sources"]:
            raise ValueError("Query source is absent from the corpus")
        expected = set(case["expected_ids"])
        confusable = set(case["confusable_ids"])
        if expected & confusable or len(expected) != len(case["expected_ids"]):
            raise ValueError("Invalid expected/confusable labels")
        if not expected:
            if (
                case["task"] != "no_match"
                or case.get("negative_domain") not in {"in_domain", "unrelated"}
                or case["evidence"]
            ):
                raise ValueError(
                    "Negative query requires an explicit domain and no labels"
                )
        elif not any(
            entities.get(key, {}).get("source") == case["source"] for key in expected
        ):
            raise ValueError("Positive query has no expected entity in its source")
        for entity_id in expected | confusable:
            if entity_id not in entities:
                raise ValueError("Fixture references an absent entity")
            if entity_splits.setdefault(entity_id, split) != split:
                raise ValueError("Labeled entity crosses development/evaluation")
        supported: set[str] = set()
        for reference in case["evidence"]:
            entity_id = reference["entity_id"]
            if entity_id not in expected:
                raise ValueError("Evidence supports an unexpected entity")
            entity = json.loads(
                checked_member(
                    archive,
                    manifest,
                    "entities/" + entity_id.replace(":", "/") + ".json",
                )
            )
            pages = {page["id"]: page for page in entity["evidence"]}
            if reference["evidence_id"] not in pages or not reference["quote"]:
                raise ValueError("Fixture evidence reference is missing")
            page = pages[reference["evidence_id"]]
            field, quote = reference["field"], reference["quote"]
            if field == "title":
                valid = quote == entity["title"]
            elif field == "alias":
                valid = quote in entity["aliases"]
            elif field == "evidence":
                valid = quote in page["markdown"] or quote in page.get(
                    "search_text", ""
                )
            else:
                valid = False
            if not valid:
                raise ValueError("Fixture quote is not supported by the archived field")
            supported.add(entity_id)
        if supported != expected:
            raise ValueError("Every positive label requires archived evidence")
    if splits != {"development", "evaluation"}:
        raise ValueError("Both development and evaluation queries are required")
    return entities


def summarize(rows: list[dict]) -> dict:
    positives = [row for row in rows if row["expected_ids"]]
    negatives = [row for row in rows if not row["expected_ids"]]
    confusable = [row for row in positives if row["confusable_ids"]]
    result: dict = {
        "positive_queries": len(positives),
        "positive_entity_groups": len({row["entity_group"] for row in positives}),
        "negative_queries": len(negatives),
    }
    for mode in ("baseline", "candidate"):
        metrics: dict = {}
        for name, limit in (("top1", 1), ("recall_at_5", 5)):
            hits = [
                row for row in positives if 0 < (row[mode]["rank"] or math.inf) <= limit
            ]
            metrics[f"ranked_{name}"] = proportion(len(hits), len(positives))
            metrics[f"accepted_{name}"] = proportion(
                sum(row[mode]["match_status"] == "candidates" for row in hits),
                len(positives),
            )
        metrics["false_acceptance"] = proportion(
            sum(row[mode]["match_status"] == "candidates" for row in negatives),
            len(negatives),
        )
        metrics["wrong_variant_top1"] = proportion(
            sum(
                row[mode]["match_status"] == "candidates"
                and row[mode]["first"] in row["confusable_ids"]
                for row in confusable
            ),
            len(confusable),
        )
        metrics["ranked_mrr_at_20"] = (
            sum(1 / row[mode]["rank"] for row in positives if row[mode]["rank"])
            / len(positives)
            if positives
            else None
        )
        result[mode] = metrics
    result["improved_ranks"] = sum(
        (row["candidate"]["rank"] or math.inf) < (row["baseline"]["rank"] or math.inf)
        for row in positives
    )
    result["worse_ranks"] = sum(
        (row["candidate"]["rank"] or math.inf) > (row["baseline"]["rank"] or math.inf)
        for row in positives
    )
    return result


def validate_response(
    response: dict,
    entities: dict,
    archive: zipfile.ZipFile,
    manifest: dict,
    source: str,
) -> None:
    if response.get("query_type") != "text" or response.get("match_status") not in {
        "candidates",
        "no_supported_match",
    }:
        raise ValueError("Invalid text query response envelope")
    results = response["results"]
    if len(results) > 20 or len({item["id"] for item in results}) != len(results):
        raise ValueError("Duplicate or excess entity results")
    for item in results:
        entity_id = item["id"]
        if entity_id not in entities or item["source"] != entities[entity_id]["source"]:
            raise ValueError("Result references an absent or misidentified entity")
        if source and item["source"] != source:
            raise ValueError("Source filter leaked an unrelated entity")
        if not all(
            isinstance(item.get(key), (int, float)) and math.isfinite(item[key])
            for key in ("score", "cosine")
        ):
            raise ValueError("Nonfinite or missing text ranking score")
        entity = json.loads(
            checked_member(
                archive, manifest, "entities/" + entity_id.replace(":", "/") + ".json"
            )
        )
        pages = {page["id"]: page for page in entity["evidence"]}
        if not isinstance(item.get("evidence_id"), str) or (
            item["evidence_id"] and item["evidence_id"] not in pages
        ):
            raise ValueError("Result has unrelated evidence")
        if not item.get("matches"):
            raise ValueError("Result has no evidence contributions")
        for match in item["matches"]:
            page = pages.get(match["evidence_id"])
            if (
                not page
                or match["channel"] not in {"text", "lexical"}
                or match["url"] != page["url"]
                or not isinstance(match.get("score"), (float, int))
                or not math.isfinite(match["score"])
            ):
                raise ValueError("Invalid source-text evidence contribution")


def benchmark(
    baseline_binary: Path,
    candidate_binary: Path,
    baseline_bundle: Path,
    candidate_bundle: Path,
    fixture_path: Path,
    split: str,
    *,
    mode: str = "hybrid",
    selection: Path | None = None,
    runner: Callable[..., dict] | None = None,
) -> dict:
    if split not in {"development", "evaluation"} or mode not in {"hybrid", "vector"}:
        raise ValueError("Invalid split or candidate search mode")
    fixture = json.loads(fixture_path.read_bytes())
    binaries = {"baseline": baseline_binary, "candidate": candidate_binary}
    bundles = {"baseline": baseline_bundle, "candidate": candidate_bundle}
    with (
        zipfile.ZipFile(baseline_bundle) as before,
        zipfile.ZipFile(candidate_bundle) as after,
    ):
        archives = {"baseline": before, "candidate": after}
        manifests = {
            name: json.loads(archive.read("manifest.json"))
            for name, archive in archives.items()
        }
        entities = validate_fixture(fixture, before, manifests["baseline"])
        validate_fixture(fixture, after, manifests["candidate"])
        for name in ("index.json", "chunks.json", "vectors.f32"):
            if manifests["baseline"]["files"].get(name) != manifests["candidate"][
                "files"
            ].get(name):
                raise ValueError(
                    "Comparison requires identical source text and vectors"
                )
        identity = {
            "fixture_sha256": sha256(fixture_path),
            "source_content_sha256": fixture["source_content_sha256"],
            "candidate_mode": mode,
            "artifacts": {
                name: {
                    "binary_sha256": sha256(binaries[name]),
                    "bundle_sha256": sha256(bundles[name]),
                    "dataset_id": manifest["dataset_id"],
                    "recipe_sha256": manifest["recipe_sha256"],
                    "search": manifest.get("search"),
                }
                for name, manifest in manifests.items()
            },
        }
        frozen = None
        if split == "evaluation":
            if selection is None:
                raise ValueError("Evaluation requires a frozen development selection")
            frozen = json.loads(selection.read_bytes())
            development_path = Path(frozen.get("development_report", ""))
            if (
                frozen.get("selection_split") != "development"
                or frozen.get("identity") != identity
                or not development_path.is_file()
                or sha256(development_path) != frozen.get("development_report_sha256")
            ):
                raise ValueError(
                    "Frozen development selection does not match the artifacts"
                )
            development = json.loads(development_path.read_bytes())
            if (
                development.get("split") != "development"
                or development.get("identity") != identity
            ):
                raise ValueError(
                    "Selection does not reference a matching development report"
                )

        def run(binary: Path, *args: str) -> dict:
            completed = subprocess.run(
                [str(binary.resolve()), *args],
                check=True,
                capture_output=True,
                timeout=120,
            )
            return json.loads(completed.stdout)

        execute = runner or run
        for name, binary in binaries.items():
            info = execute(binary, "info")
            if info["dataset_id"] != manifests[name]["dataset_id"]:
                raise ValueError("Executable does not contain its declared bundle")
        reports = []
        for case in fixture["cases"]:
            if case["split"] != split:
                continue
            for scope, source in (("global", ""), ("source_filtered", case["source"])):
                expected = [
                    key
                    for key in case["expected_ids"]
                    if not source or entities[key]["source"] == source
                ]
                confusable = [
                    key
                    for key in case["confusable_ids"]
                    if not source or entities[key]["source"] == source
                ]
                row = {
                    "id": case["id"],
                    "scope": scope,
                    "source": case["source"],
                    "query": case["query"],
                    "task": case["task"],
                    "entity_group": case["entity_group"],
                    "expected_ids": expected,
                    "confusable_ids": confusable,
                    "negative_domain": case.get("negative_domain"),
                }
                for name, binary in binaries.items():
                    args = [
                        "search",
                        "--mode",
                        "hybrid" if name == "baseline" else mode,
                        "--limit",
                        "20",
                    ]
                    if source:
                        args.extend(["--source", source])
                    started = time.perf_counter()
                    response = execute(binary, *args, case["query"])
                    elapsed = time.perf_counter() - started
                    validate_response(
                        response, entities, archives[name], manifests[name], source
                    )
                    items = response["results"]
                    row[name] = {
                        "rank": next(
                            (
                                i + 1
                                for i, item in enumerate(items)
                                if item["id"] in expected
                            ),
                            None,
                        ),
                        "first": items[0]["id"] if items else None,
                        "match_status": response["match_status"],
                        "calibration_status": response.get(
                            "calibration_status", "unavailable"
                        ),
                        "seconds": elapsed,
                        "top5": items[:5],
                    }
                reports.append(row)

    def scopes(rows: list[dict]) -> dict:
        return {
            scope: summarize([row for row in rows if row["scope"] == scope])
            for scope in ("global", "source_filtered")
        }

    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "identity": identity,
        "split": split,
        "selection": frozen,
        "cases": reports,
        "excluded": [],
        "metrics": scopes(reports),
        "by_task": {
            task: scopes([row for row in reports if row["task"] == task])
            for task in sorted({row["task"] for row in reports})
        },
        "by_source": {
            source: scopes([row for row in reports if row["source"] == source])
            for source in sorted({row["source"] for row in reports})
        },
        "by_negative_domain": {
            domain: scopes([row for row in reports if row["negative_domain"] == domain])
            for domain in ("in_domain", "unrelated")
        },
        "release_quality_established": False,
        "limitations": [
            *fixture["limitations"],
            "Legacy candidates are ranking output without calibrated identity confidence; compare accepted counts with that distinction.",
            "Latencies here are observational; this interleaved quality run is not the isolated resource benchmark.",
        ],
    }


def freeze_selection(report_path: Path, selection_path: Path) -> None:
    report = json.loads(report_path.read_bytes())
    if report["split"] != "development":
        raise ValueError("Only development results may freeze a selection")
    if selection_path.exists():
        raise ValueError("A frozen selection cannot be overwritten")
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(
        json.dumps(
            {
                "selection_split": "development",
                "created_at": datetime.now(UTC).isoformat(),
                "identity": report["identity"],
                "development_report": str(report_path.resolve()),
                "development_report_sha256": sha256(report_path),
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline_binary", type=Path)
    parser.add_argument("candidate_binary", type=Path)
    parser.add_argument("--baseline-bundle", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=ROOT / "tests/fixtures/ranking.json"
    )
    parser.add_argument("--split", choices=("development", "evaluation"), required=True)
    parser.add_argument("--mode", choices=("hybrid", "vector"), default="hybrid")
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--freeze-selection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.freeze_selection and args.split != "development":
        parser.error("--freeze-selection requires --split development")
    report = benchmark(
        args.baseline_binary,
        args.candidate_binary,
        args.baseline_bundle,
        args.bundle,
        args.fixture,
        args.split,
        mode=args.mode,
        selection=args.selection,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if args.freeze_selection:
        freeze_selection(args.output, args.freeze_selection)
    print(json.dumps({"output": str(args.output), "metrics": report["metrics"]}))


if __name__ == "__main__":
    main()
