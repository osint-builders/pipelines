"""Measure image and combined CLI ranking against the frozen, isolated seed gallery."""

import argparse
import hashlib
import json
import subprocess
import time
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from accept_cli import contains_query_path, validate_image_response
from benchmark_images import ROOT, checked_image, proportion, validate_fixture


def gallery_contract(archive: zipfile.ZipFile, fixture: dict) -> tuple[dict, dict]:
    media = validate_fixture(fixture)
    expected: dict[str, set[str]] = {}
    for item in media.values():
        if item["split"] == "gallery" and item["status"] == "captured":
            expected.setdefault(item["sha256"], set()).update(item["entity_ids"])
    if not expected:
        raise ValueError("The benchmark requires a captured gallery")
    manifest = json.loads(archive.read("manifest.json"))
    raw = archive.read("image/index.json")
    if hashlib.sha256(raw).hexdigest() != manifest["image"]["gallery_sha256"]:
        raise ValueError("Image gallery checksum does not match the bundle")
    records = json.loads(raw)
    actual: dict[str, set[str]] = {}
    for row in records:
        if row["vector_index"] is not None:
            actual.setdefault(row["sha256"], set()).update(
                ref["entity_id"] for ref in row["references"]
            )
    if actual != expected:
        raise ValueError(
            "Benchmark bundle must contain only the frozen gallery and associations"
        )
    if "search" in manifest:
        captions = archive.read("search/captions.json")
        if (
            hashlib.sha256(captions).hexdigest()
            != manifest["files"]["search/captions.json"]
        ):
            raise ValueError("Caption checksum does not match the benchmark bundle")
        allowed = {row["id"] for row in records if row["vector_index"] is not None}
        if any(row["media_id"] not in allowed for row in json.loads(captions)):
            raise ValueError("Benchmark captions must belong to the frozen gallery")
    return manifest, {row["id"]: row for row in records}


def summarize(rows: list[dict]) -> dict:
    positives = [row for row in rows if row["expected_ids"]]
    negatives = [row for row in rows if not row["expected_ids"]]
    result: dict = {
        "positive_cases": len(positives),
        "independent_photo_groups": len({row["photo_group"] for row in positives}),
        "negative_cases": len(negatives),
    }
    for name, limit in (("top1", 1), ("recall_at_5", 5)):
        ranked = [
            row for row in positives if row["rank"] is not None and row["rank"] <= limit
        ]
        accepted = [row for row in ranked if row["match_status"] == "candidates"]
        result[f"ranked_{name}"] = proportion(len(ranked), len(positives))
        result[f"accepted_{name}"] = proportion(len(accepted), len(positives))
    result["false_acceptance"] = proportion(
        sum(row["match_status"] == "candidates" for row in negatives), len(negatives)
    )
    return result


def validate_response(
    response: dict,
    manifest: dict,
    records: dict,
    picture: dict,
    source: str,
    combined: bool,
) -> None:
    items = validate_image_response(
        response, manifest, picture["sha256"], combined, source=source, limit=20
    )
    for item in items:
        visual = [match for match in item["matches"] if match["channel"] == "image"]
        for match in visual:
            record = records[match["media_id"]]
            if record["vector_index"] is None or not any(
                ref["entity_id"] == item["id"]
                and ref["evidence_id"] == match["evidence_id"]
                for ref in record["references"]
            ):
                raise ValueError("Image contribution references unrelated media")


def benchmark(
    binary: Path,
    bundle: Path,
    fixture_path: Path,
    split: str,
    *,
    selection: Path | None = None,
    root: Path = ROOT,
    runner: Callable[..., dict] | None = None,
) -> dict:
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    media = validate_fixture(fixture)
    with zipfile.ZipFile(bundle) as archive:
        manifest, records = gallery_contract(archive, fixture)
    identity = {
        "fixture_sha256": hashlib.sha256(fixture_bytes).hexdigest(),
        "dataset_id": manifest["dataset_id"],
        "gallery_sha256": manifest["image"]["gallery_sha256"],
        "model_sha256": manifest["image"]["model_sha256"],
        "search": manifest["image"]["search"],
    }
    frozen = None
    binary_hash = (
        hashlib.sha256(binary.read_bytes()).hexdigest() if binary.is_file() else None
    )
    if split == "evaluation":
        if selection is None:
            raise ValueError("Evaluation requires a frozen development selection")
        frozen = json.loads(selection.read_bytes())
        if (
            frozen.get("selection_split") != "development"
            or frozen.get("identity") != identity
            or (
                (binary_hash is not None or "binary_sha256" in frozen)
                and frozen.get("binary_sha256") != binary_hash
            )
        ):
            raise ValueError(
                "Frozen development selection does not match this bundle or executable"
            )

    def run(*args: str) -> dict:
        completed = subprocess.run(
            [str(binary.resolve()), *args], check=True, capture_output=True, timeout=120
        )
        return json.loads(completed.stdout)

    execute = runner or run
    info = execute("info")
    if info["dataset_id"] != manifest["dataset_id"]:
        raise ValueError("Executable does not contain the benchmark bundle")
    rows, excluded = [], []
    for case in fixture["cases"]:
        if case["split"] != split:
            continue
        picture_id = case["query"].get("image_id")
        if case["status"] != "ready" or not picture_id:
            excluded.append(
                {
                    "id": case["id"],
                    "reason": case["status"] if picture_id else "text-only",
                }
            )
            continue
        picture = media[picture_id]
        checked_image(picture, root)
        path = str((root / picture["local_path"]).resolve())
        combined = bool(case["query"].get("text"))
        scopes = [("global", "")]
        if source := case["query"].get("source"):
            scopes.append(("source_filtered", source))
        for scope, source in scopes:
            args = ["search", "--image", path, "--limit", "20"]
            if source:
                args.extend(["--source", source])
            if combined:
                args.append(case["query"]["text"])
            started = time.perf_counter()
            response = execute(*args)
            seconds = time.perf_counter() - started
            validate_response(response, manifest, records, picture, source, combined)
            if contains_query_path(response, path):
                raise ValueError("Response exposed the local query path")
            items = response["results"]
            rows.append(
                {
                    "id": case["id"],
                    "scope": scope,
                    "source": source or "unscoped",
                    "task": case["task"],
                    "mode": "image_text" if combined else "image",
                    "photo_group": picture["photo_group"],
                    "expected_ids": case["expected_ids"],
                    "rank": next(
                        (
                            i + 1
                            for i, item in enumerate(items)
                            if item["id"] in case["expected_ids"]
                        ),
                        None,
                    ),
                    "match_status": response["match_status"],
                    "seconds": seconds,
                    "top5": items[:5],
                }
            )
    return {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "split": split,
        "identity": identity,
        "binary_sha256": binary_hash,
        "selection": frozen,
        "excluded": excluded,
        "cases": rows,
        "metrics": {
            scope: {
                mode: summarize(
                    [
                        row
                        for row in rows
                        if row["scope"] == scope and row["mode"] == mode
                    ]
                )
                for mode in ("image", "image_text")
            }
            for scope in ("global", "source_filtered")
        },
        "release_quality_established": False,
        "limitations": [
            "Frozen nine-entity image gallery; text still searches the complete bundled text corpus.",
            "Ranked suggestions and accepted matches are reported separately. Uncalibrated no_supported_match is not a successful positive identification.",
            "Seed counts and correlated photographs cannot establish release acceptance or a false-match rate.",
            "The broad archive build may include these query photographs; only this restricted gallery supports held-out measurements.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=ROOT / "tests/fixtures/multimodal.json"
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
    print(
        json.dumps(
            {
                "output": str(args.output),
                "cases": len(report["cases"]),
                "metrics": report["metrics"],
            }
        )
    )


if __name__ == "__main__":
    main()
