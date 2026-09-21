"""Compare source-only and generated-text rankings on frozen manual image queries."""

import argparse
import hashlib
import json
import subprocess
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from accept_cli import bundle_evidence, validate_observation_response
from benchmark_image_cli import gallery_contract
from benchmark_images import ROOT, proportion


def summarize(rows: list[dict]) -> dict:
    positives = [row for row in rows if row["expected_ids"]]
    negatives = [row for row in rows if not row["expected_ids"]]
    result: dict = {
        "positive_queries": len(positives),
        "positive_photo_groups": len({row["photo_group"] for row in positives}),
        "negative_queries": len(negatives),
    }
    for mode in ("source", "observations"):
        for name, limit in (("ranked_top1", 1), ("ranked_recall_at_5", 5)):
            count = sum(
                row[mode]["rank"] is not None and row[mode]["rank"] <= limit
                for row in positives
            )
            result[f"{mode}_{name}"] = proportion(count, len(positives))
        result[f"{mode}_accepted_top1"] = proportion(
            sum(
                row[mode]["rank"] == 1 and row[mode]["match_status"] == "candidates"
                for row in positives
            ),
            len(positives),
        )
        result[f"{mode}_false_acceptance"] = proportion(
            sum(row[mode]["match_status"] == "candidates" for row in negatives),
            len(negatives),
        )
    result["improved_ranks"] = sum(
        (row["observations"]["rank"] or 10**9) < (row["source"]["rank"] or 10**9)
        for row in positives
    )
    result["worse_ranks"] = sum(
        (row["observations"]["rank"] or 10**9) > (row["source"]["rank"] or 10**9)
        for row in positives
    )
    return result


def validate_response(
    response: dict,
    manifest: dict,
    rows: dict,
    recipes: dict,
    evidence: dict[str, dict[str, str]],
    *,
    enabled: bool,
    source: str,
) -> None:
    validate_observation_response(
        response, manifest, rows, recipes, evidence, enabled=enabled, source=source
    )


def benchmark(
    binary: Path,
    bundle: Path,
    fixture_path: Path,
    split: str,
    *,
    selection: Path | None = None,
    runner: Callable[..., dict] | None = None,
) -> dict:
    fixture_body = fixture_path.read_bytes()
    fixture = json.loads(fixture_body)
    gallery_path = ROOT / fixture["gallery_fixture"]
    gallery_body = gallery_path.read_bytes()
    if hashlib.sha256(gallery_body).hexdigest() != fixture["gallery_fixture_sha256"]:
        raise ValueError("The frozen image fixture changed")
    with zipfile.ZipFile(bundle) as archive:
        manifest, _ = gallery_contract(archive, json.loads(gallery_body))
        if manifest.get("format_version") != 4 or "observations" not in manifest:
            raise ValueError("The benchmark requires an observation bundle")
        artifacts = {}
        for name in ("observations/index.json", "observations/recipes.json"):
            body = archive.read(name)
            if hashlib.sha256(body).hexdigest() != manifest["files"].get(name):
                raise ValueError("Observation benchmark artifact checksum mismatch")
            artifacts[name] = json.loads(body)
        if (
            manifest["observations"]["index_sha256"]
            != manifest["files"]["observations/index.json"]
        ):
            raise ValueError("Observation benchmark index identity mismatch")
        observations = artifacts["observations/index.json"]
        recipes = artifacts["observations/recipes.json"]
        evidence = bundle_evidence(archive)
    identity = {
        "fixture_sha256": hashlib.sha256(fixture_body).hexdigest(),
        "dataset_id": manifest["dataset_id"],
        "gallery_sha256": manifest["image"]["gallery_sha256"],
        "observations_sha256": manifest["observations"]["index_sha256"],
        "recipes_sha256": manifest["files"]["observations/recipes.json"],
        "search": manifest["observations"]["search"],
    }
    binary_sha = (
        hashlib.sha256(binary.read_bytes()).hexdigest() if binary.is_file() else None
    )
    frozen = None
    if split == "evaluation":
        if selection is None:
            raise ValueError("Evaluation requires frozen development choices")
        frozen = json.loads(selection.read_bytes())
        if (
            frozen.get("identity") != identity
            or frozen.get("selection_split") != "development"
            or frozen.get("binary_sha256") != binary_sha
        ):
            raise ValueError("Frozen development choices do not match this executable")

    def run(*args: str) -> dict:
        result = subprocess.run(
            [str(binary.resolve()), *args], capture_output=True, check=True, timeout=120
        )
        return json.loads(result.stdout)

    execute = runner or run
    info = execute("info")
    if info["dataset_id"] != manifest["dataset_id"] or not info.get(
        "observations_available"
    ):
        raise ValueError("Executable does not contain observation bundle")
    by_id = {row["id"]: row for row in observations}
    reports = []
    for case in fixture["cases"]:
        if case["split"] != split:
            continue
        scopes = [("global", "")]
        if case["source"]:
            scopes.append(("source_filtered", case["source"]))
        for scope, source in scopes:
            row = {**case, "scope": scope}
            for enabled in (False, True):
                args = ["search", "--limit", "20"]
                if enabled:
                    args.append("--observations")
                if source:
                    args.extend(["--source", source])
                started = time.perf_counter()
                response = execute(*args, case["query"])
                elapsed = time.perf_counter() - started
                validate_response(
                    response,
                    manifest,
                    by_id,
                    recipes,
                    evidence,
                    enabled=enabled,
                    source=source,
                )
                items = response["results"]
                row["observations" if enabled else "source"] = {
                    "rank": next(
                        (
                            i + 1
                            for i, item in enumerate(items)
                            if item["id"] in case["expected_ids"]
                        ),
                        None,
                    ),
                    "match_status": response["match_status"],
                    "top5": items[:5],
                    "seconds": elapsed,
                }
            reports.append(row)
    return {
        "schema_version": 1,
        "identity": identity,
        "binary_sha256": binary_sha,
        "split": split,
        "selection": frozen,
        "cases": reports,
        "metrics": {
            scope: summarize([row for row in reports if row["scope"] == scope])
            for scope in ("global", "source_filtered")
        },
        "by_task": {
            task: summarize(
                [
                    row
                    for row in reports
                    if row["scope"] == "global" and row["task"] == task
                ]
            )
            for task in ("ocr", "description", "negative")
        },
        "release_quality_established": False,
        "limitations": [
            *fixture["limitations"],
            "Generated suggestions always abstain; ranked improvements do not establish accepted identification quality.",
            "Source-only legacy candidates are ranking output without calibrated identity confidence; compare acceptance counts with this distinction.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=ROOT / "tests/fixtures/observations.json"
    )
    parser.add_argument("--split", choices=("development", "evaluation"), required=True)
    parser.add_argument("--selection", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(
        args.binary, args.bundle, args.fixture, args.split, selection=args.selection
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(args.output), "metrics": report["metrics"]}))


if __name__ == "__main__":
    main()
