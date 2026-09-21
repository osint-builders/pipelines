"""Compare offline image encoders on the frozen seed gallery, without abstention."""

import argparse
import hashlib
import json
import math
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

ROOT = Path(__file__).resolve().parents[1]


class ImageEncoder(Protocol):
    def encode(self, encoded: bytes) -> NDArray[np.float32]: ...


def create_encoder(directory: Path, manifest: dict) -> ImageEncoder:
    from pipelines.image_embedding import Encoder

    return Encoder(directory, manifest)


def validate_fixture(fixture: dict) -> dict[str, dict]:
    media = {item["id"]: item for item in fixture["media"]}
    if len(media) != len(fixture["media"]):
        raise ValueError("Duplicate media IDs")
    groups: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for item in media.values():
        split = item["split"]
        if split not in {"gallery", "development", "evaluation"}:
            raise ValueError("Unknown media split")
        if groups.setdefault(item["photo_group"], split) != split:
            raise ValueError("Photo-group leakage across splits")
        if item.get("sha256") and hashes.setdefault(item["sha256"], split) != split:
            raise ValueError("Image hash leakage across splits")
        ancestry = {item["id"]}
        current = item
        while current.get("crop_from"):
            parent_id = current["crop_from"]
            if parent_id in ancestry or parent_id not in media:
                raise ValueError("Invalid derivative ancestry")
            ancestry.add(parent_id)
            parent = media[parent_id]
            if (parent["split"], parent["photo_group"]) != (
                item["split"],
                item["photo_group"],
            ):
                raise ValueError("Derivative split/photo-group leakage")
            current = parent
    seen = set()
    for case in fixture["cases"]:
        if case["id"] in seen:
            raise ValueError("Duplicate case IDs")
        seen.add(case["id"])
        if image_id := case["query"].get("image_id"):
            if image_id not in media:
                raise ValueError("Query image is absent from the fixture")
            if media[image_id]["split"] != case["split"] or case["split"] == "gallery":
                raise ValueError("Query/gallery split leakage")
        if set(case["expected_ids"]) & set(case.get("confusable_ids", [])):
            raise ValueError("Expected and confusable IDs overlap")
    return media


def checked_image(item: dict, root: Path) -> bytes:
    path = (root / item["local_path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Image cache path escapes the repository")
    body = path.read_bytes()
    if len(body) != item["bytes"] or hashlib.sha256(body).hexdigest() != item["sha256"]:
        raise ValueError(f"Image cache checksum/size mismatch: {item['id']}")
    return body


def proportion(numerator: int, denominator: int) -> dict:
    if not denominator:
        return {
            "numerator": numerator,
            "denominator": 0,
            "rate": None,
            "wilson95": None,
        }
    rate, z = numerator / denominator, 1.959963984540054
    scale = 1 + z * z / denominator
    center = (rate + z * z / (2 * denominator)) / scale
    half = (
        z
        * math.sqrt(rate * (1 - rate) / denominator + z * z / (4 * denominator**2))
        / scale
    )
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": rate,
        "wilson95": [max(0.0, center - half), min(1.0, center + half)],
    }


def metrics(rows: list[dict], scope: str) -> dict:
    positives = [row for row in rows if row["expected_ids"] and row[scope] is not None]
    groups = sorted({row["photo_group"] for row in positives})
    output: dict = {
        "positive_cases": len(positives),
        "unique_photo_groups": len(groups),
    }
    for key, limit in (("top1", 1), ("recall_at_5", 5)):
        hits = {
            row["id"]: 0 < (row[scope]["rank"] or math.inf) <= limit
            for row in positives
        }
        output[key] = proportion(sum(hits.values()), len(positives))
        output[f"{key}_all_cases_per_photo_group"] = proportion(
            sum(
                all(hits[row["id"]] for row in positives if row["photo_group"] == group)
                for group in groups
            ),
            len(groups),
        )
    return output


def summarize(rows: list[dict]) -> dict:
    return {
        scope: {
            "overall": metrics(rows, scope),
            **{
                f"by_{key}": {
                    label: metrics([row for row in rows if row[key] == label], scope)
                    for label in sorted({row[key] for row in rows})
                }
                for key in ("task", "source")
            },
        }
        for scope in ("global", "source_filtered")
    }


def rank_entities(
    gallery: list[dict],
    scores: NDArray[np.float32],
    expected: list[str],
    source: str | None = None,
) -> dict:
    best: dict[str, dict] = {}
    for item, score in zip(gallery, scores, strict=True):
        for entity in item["entity_ids"]:
            if source and entity.split(":", 1)[0] != source:
                continue
            result = {
                "id": entity,
                "media_id": item["id"],
                "cosine": float(np.clip(score, -1, 1)),
            }
            previous = best.get(entity)
            if previous is None or (-result["cosine"], result["media_id"]) < (
                -previous["cosine"],
                previous["media_id"],
            ):
                best[entity] = result
    results = sorted(best.values(), key=lambda row: (-row["cosine"], row["id"]))
    return {
        "rank": next(
            (i + 1 for i, row in enumerate(results) if row["id"] in expected), None
        ),
        "results": results,
    }


def normalize(vector: NDArray, dimensions: int) -> NDArray[np.float32]:
    raw = np.asarray(vector, dtype=np.float32)
    norm = float(np.linalg.norm(raw.astype(np.float64)))
    if raw.shape != (dimensions,) or not np.isfinite(raw).all() or not norm > 1e-12:
        raise ValueError("Encoder returned an invalid vector")
    return (raw.astype(np.float64) / norm).astype(np.float32)


def benchmark(
    manifests: list[Path],
    fixture_path: Path,
    split: str,
    *,
    selection: Path | None = None,
    root: Path = ROOT,
    encoder_factory: Callable[[Path, dict], ImageEncoder] = create_encoder,
) -> dict:
    if split not in {"development", "evaluation"} or not manifests:
        raise ValueError(
            "Select development/evaluation and at least one model manifest"
        )
    if split == "evaluation" and selection is None:
        raise ValueError("Evaluation requires a frozen development selection")
    fixture_bytes = fixture_path.read_bytes()
    fixture = json.loads(fixture_bytes)
    media = validate_fixture(fixture)
    gallery = sorted(
        (
            item
            for item in media.values()
            if item["split"] == "gallery" and item["status"] == "captured"
        ),
        key=lambda item: item["id"],
    )
    if not gallery or any(not item["entity_ids"] for item in gallery):
        raise ValueError("A captured, entity-associated gallery is required")
    cases, excluded = [], []
    for case in fixture["cases"]:
        if case["split"] != split:
            continue
        query = case["query"]
        reason = None
        if case["status"] != "ready":
            reason = case["status"]
        elif "image_id" not in query or "text" in query or case["task"] == "image_text":
            reason = "not_image_only"
        elif media[query["image_id"]]["status"] != "captured":
            reason = "image_not_captured"
        if reason:
            excluded.append({"id": case["id"], "reason": reason})
        else:
            cases.append(case)
    if not cases:
        raise ValueError("No ready image-only cases in the selected split")
    used_ids = {item["id"] for item in gallery} | {
        case["query"]["image_id"] for case in cases
    }
    gallery_identity = [
        {
            key: item[key]
            for key in ("id", "sha256", "bytes", "photo_group", "entity_ids")
        }
        for item in gallery
    ]
    fixture_sha256 = hashlib.sha256(fixture_bytes).hexdigest()
    gallery_sha256 = hashlib.sha256(
        json.dumps(gallery_identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    prepared = []
    for path in manifests:
        raw = path.read_bytes()
        manifest = json.loads(raw)
        model_path = (path.parent / manifest["file"]).resolve()
        if not model_path.is_relative_to(path.parent.resolve()):
            raise ValueError("Model file escapes manifest directory")
        with model_path.open("rb") as stream:
            checksum = hashlib.file_digest(stream, "sha256").hexdigest()
        if (
            checksum != manifest["sha256"]
            or model_path.stat().st_size != manifest["bytes"]
        ):
            raise ValueError("Model checksum/size mismatch")
        prepared.append((path, raw, manifest))
    selection_record, selection_sha256 = None, None
    if selection is not None:
        selection_bytes = selection.read_bytes()
        selection_record = json.loads(selection_bytes)
        if (
            selection_record.get("schema_version") != 1
            or selection_record.get("selection_split") != "development"
            or selection_record.get("fixture_sha256") != fixture_sha256
            or selection_record.get("gallery_sha256") != gallery_sha256
            or selection_record.get("selected_model_sha256")
            not in {manifest["sha256"] for _, _, manifest in prepared}
        ):
            raise ValueError(
                "Frozen selection does not match this development fixture, gallery and model set"
            )
        selection_sha256 = hashlib.sha256(selection_bytes).hexdigest()
    bodies = {key: checked_image(media[key], root) for key in sorted(used_ids)}
    models = []
    for path, raw, manifest in prepared:
        start = time.perf_counter()
        encoder = encoder_factory(path.parent, manifest)
        load_seconds = time.perf_counter() - start
        vectors: dict[str, NDArray[np.float32]] = {}
        times = []
        for key in sorted(used_ids):
            digest = media[key]["sha256"]
            if digest not in vectors:
                start = time.perf_counter()
                vectors[digest] = normalize(
                    encoder.encode(bodies[key]), manifest["dimensions"]
                )
                times.append({"media_id": key, "seconds": time.perf_counter() - start})
        matrix = np.stack([vectors[item["sha256"]] for item in gallery])
        representations = {}
        for storage in ("float32", "float16"):
            stored = (
                matrix
                if storage == "float32"
                else matrix.astype(np.float16).astype(np.float32)
            )
            indexed = np.stack(
                [normalize(row, manifest["dimensions"]) for row in stored]
            )
            rows = []
            for case in cases:
                image = media[case["query"]["image_id"]]
                scores = indexed @ vectors[image["sha256"]]
                source = case["query"].get("source")
                rows.append(
                    {
                        "id": case["id"],
                        "task": case["task"],
                        "source": source or "unscoped",
                        "photo_group": image["photo_group"],
                        "expected_ids": case["expected_ids"],
                        "confusable_ids": case.get("confusable_ids", []),
                        "global": rank_entities(gallery, scores, case["expected_ids"]),
                        "source_filtered": rank_entities(
                            gallery, scores, case["expected_ids"], source
                        )
                        if source
                        else None,
                    }
                )
            representations[storage] = {"metrics": summarize(rows), "cases": rows}
        models.append(
            {
                "manifest": manifest,
                "manifest_sha256": hashlib.sha256(raw).hexdigest(),
                "model_sha256": manifest["sha256"],
                "load_seconds": load_seconds,
                "encoding_samples": times,
                "representations": representations,
            }
        )
        del encoder
    return {
        "schema_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "split": split,
        "fixture_sha256": fixture_sha256,
        "gallery_sha256": gallery_sha256,
        "selection": selection_record,
        "selection_sha256": selection_sha256,
        "gallery": gallery_identity,
        "gallery_entities": len(
            {entity for item in gallery for entity in item["entity_ids"]}
        ),
        "excluded": excluded,
        "negative_queries": sum(not case["expected_ids"] for case in cases),
        "abstention": {
            "implemented": False,
            "false_acceptance": None,
            "note": "Negative controls report ranked cosines only; no decision threshold or false-match rate is inferred.",
        },
        "models": models,
        "limitations": [
            "Encoder-only seed benchmark over this explicit small gallery, not the full archive or CLI. No release-quality claim.",
            "Wilson intervals summarize this seed; correlated crops and exhibition sessions violate independent-sample assumptions. Photo-group summaries require all cases in a group to succeed.",
            "Declared hashes, groups and derivative ancestry are checked across ALL splits, including excluded/mixed cases. Undeclared near-duplicates require separate review.",
            "FP16 comparison rounds only stored gallery vectors, then casts to FP32 and renormalizes. Query vectors and cosine arithmetic remain FP32; unrelated model spaces are never compared directly.",
            "Times measure in-process Python CPU encoding, including preprocessing, after one model load. They are not native Go CLI cold/warm latency or memory measurements.",
            *fixture.get("review", {}).get("limitations", []),
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", action="append", type=Path, required=True)
    parser.add_argument(
        "--fixture", type=Path, default=ROOT / "tests/fixtures/multimodal.json"
    )
    parser.add_argument("--split", choices=("development", "evaluation"), required=True)
    parser.add_argument(
        "--selection",
        type=Path,
        help="frozen development selection; required for evaluation",
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = benchmark(
        args.manifest, args.fixture, args.split, selection=args.selection
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "split": args.split,
                "models": len(report["models"]),
                "gallery_entities": report["gallery_entities"],
            }
        )
    )


if __name__ == "__main__":
    main()
