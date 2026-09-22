"""Cached image observations, kept separate from captured source statements."""

import hashlib
import json
import math
import re
import sys
import zipfile
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Protocol

from pipelines.media import MediaStore, ProcessingRecipe

MAX_OBSERVATIONS = 20_000
MAX_TEXT = 16_384
MAX_REGIONS = 256
CORE_FIELDS = ("confidence", "kind", "media_sha256", "recipe_sha256", "regions", "text")


class Analyzer(Protocol):
    @property
    def recipe(self) -> ProcessingRecipe: ...

    def analyze(self, body: bytes) -> dict: ...


def canonical(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()


def digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def is_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def validate_recipe(recipe: dict) -> None:
    if (
        set(recipe) != {"version", "model_id", "model_revision", "settings"}
        or any(
            not isinstance(recipe[key], str) or not recipe[key].strip()
            for key in ("version", "model_id", "model_revision")
        )
        or not isinstance(recipe["settings"], dict)
    ):
        raise ValueError("Observation recipe requires model and processing identity")
    canonical(recipe)


def _number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def _confidence(value: object) -> bool:
    return value is None or (_number(value) and 0 <= value <= 1)  # type: ignore[operator]


def validate_output(value: dict, kind: str, *, allow_empty: bool = False) -> None:
    if (
        kind not in {"ocr", "description"}
        or set(value) != {"text", "confidence", "regions"}
        or not isinstance(value["text"], str)
        or len(value["text"]) > MAX_TEXT
        or (not allow_empty and not value["text"].strip())
        or not _confidence(value["confidence"])
        or not isinstance(value["regions"], list)
        or len(value["regions"]) > MAX_REGIONS
    ):
        raise ValueError("Invalid generated observation")
    if kind == "description" and (value["confidence"] is not None or value["regions"]):
        raise ValueError("Descriptions have no measured confidence or image regions")
    if kind == "ocr" and bool(value["text"].strip()) != bool(value["regions"]):
        raise ValueError("OCR text requires corresponding regions")
    for region in value["regions"]:
        if (
            not isinstance(region, dict)
            or set(region) != {"text", "confidence", "polygon"}
            or not isinstance(region["text"], str)
            or not region["text"].strip()
            or len(region["text"]) > MAX_TEXT
            or not _confidence(region["confidence"])
        ):
            raise ValueError("Invalid OCR region")
        points = region["polygon"]
        if (
            not isinstance(points, list)
            or len(points) != 4
            or any(
                not isinstance(point, list)
                or len(point) != 2
                or any(not _number(v) or not 0 <= v <= 1 for v in point)
                for point in points
            )
        ):
            raise ValueError("OCR polygons must use normalized original coordinates")
        area = sum(
            points[i][0] * points[(i + 1) % 4][1]
            - points[(i + 1) % 4][0] * points[i][1]
            for i in range(4)
        )
        if abs(area) < 1e-12:
            raise ValueError("OCR polygon has no area")
    if kind == "ocr" and value["text"] != "\n".join(
        region["text"] for region in value["regions"]
    ):
        raise ValueError("OCR text must match its image regions")
    canonical(value)


def observation_id(row: dict) -> str:
    return digest(canonical({key: row[key] for key in CORE_FIELDS}))


def image_references(rows: list[dict]) -> list[dict]:
    values = {
        canonical(
            {
                "media_id": row["id"],
                "entity_id": ref["entity_id"],
                "evidence_id": ref["evidence_id"],
            }
        )
        for row in rows
        for ref in row["references"]
    }
    return [json.loads(value) for value in sorted(values)]


def validate_row(row: dict, recipes: dict, images: dict[str, dict]) -> None:
    fields = {
        "id",
        "kind",
        "origin",
        "media_sha256",
        "media_ids",
        "references",
        "recipe_sha256",
        "text",
        "confidence",
        "regions",
    }
    if set(row) != fields or row["origin"] != "generated":
        raise ValueError("Invalid observation fields or origin")
    validate_output(
        {key: row[key] for key in ("text", "confidence", "regions")}, row["kind"]
    )
    if (
        not is_digest(row["media_sha256"])
        or not is_digest(row["recipe_sha256"])
        or row["recipe_sha256"] not in recipes
        or row["id"] != observation_id(row)
    ):
        raise ValueError("Invalid observation identity or processing recipe")
    ids = row["media_ids"]
    if not isinstance(ids, list) or not ids or ids != sorted(set(ids)):
        raise ValueError("Observation media IDs must be unique and sorted")
    selected = []
    for identifier in ids:
        media = images.get(identifier)
        if (
            media is None
            or media["vector_index"] is None
            or media["sha256"] != row["media_sha256"]
        ):
            raise ValueError("Observation references unrelated or unindexed media")
        selected.append(media)
    if row["references"] != image_references(selected):
        raise ValueError("Observation references do not match captured evidence")


def validate_analysis(value: dict, manifest: dict, images: list[dict]) -> None:
    if (
        value.get("schema_version") != 1
        or value.get("gallery_sha256") != manifest["image"]["gallery_sha256"]
        or not isinstance(value.get("recipes"), dict)
        or not value["recipes"]
        or not isinstance(value.get("observations"), list)
        or len(value["observations"]) > MAX_OBSERVATIONS
        or not isinstance(value.get("outcomes"), list)
        or not isinstance(value.get("kinds"), list)
        or not value["kinds"]
        or any(kind not in {"ocr", "description"} for kind in value["kinds"])
        or value["kinds"] != sorted(set(value["kinds"]))
    ):
        raise ValueError("Observation analysis does not match the image gallery")
    for identity, recipe in value["recipes"].items():
        validate_recipe(recipe)
        if identity != digest(canonical(recipe)):
            raise ValueError("Observation recipe checksum mismatch")
    by_id = {row["id"]: row for row in images}
    rows = value["observations"]
    if [row["id"] for row in rows] != sorted({row["id"] for row in rows}):
        raise ValueError("Observation IDs must be unique and sorted")
    for row in rows:
        validate_row(row, value["recipes"], by_id)
    identities = {}
    by_observation = {row["id"]: row for row in rows}
    for outcome in value["outcomes"]:
        if not isinstance(outcome, dict):
            raise ValueError("Invalid analysis outcome")
        fields = {"media_id", "kind", "state"}
        if outcome.get("state") == "observed":
            fields.add("observation_id")
        elif outcome.get("state") == "failed":
            fields.add("reason")
            reason = outcome.get("reason")
            if (
                not isinstance(reason, str)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", reason) is None
            ):
                raise ValueError("Failed analysis outcome requires an error class")
        if set(outcome) != fields:
            raise ValueError("Invalid analysis outcome fields")
        media = by_id.get(outcome.get("media_id"))
        key = (outcome.get("media_id"), outcome.get("kind"))
        if (
            media is None
            or media["vector_index"] is None
            or key in identities
            or outcome.get("kind") not in {"ocr", "description"}
            or outcome.get("state") not in {"observed", "empty", "failed", "selection"}
        ):
            raise ValueError("Invalid or duplicate analysis outcome")
        identities[key] = outcome
        if outcome["state"] == "observed":
            row = by_observation.get(outcome.get("observation_id"))
            if (
                row is None
                or media["id"] not in row["media_ids"]
                or row["kind"] != key[1]
            ):
                raise ValueError("Analysis outcome references unrelated observation")
    for row in rows:
        if any(
            identities.get((identifier, row["kind"]), {}).get("state") != "observed"
            or identities.get((identifier, row["kind"]), {}).get("observation_id")
            != row["id"]
            for identifier in row["media_ids"]
        ):
            raise ValueError("Observation is missing its analysis outcome")
    if set(identities) != {
        (row["id"], kind)
        for row in images
        if row["vector_index"] is not None
        for kind in value["kinds"]
    }:
        raise ValueError("Analysis outcomes must cover every indexed image and kind")


def observe(
    root: Path,
    bundle: Path,
    output: Path,
    *,
    ocr_model: Path | None = None,
    description_model: Path | None = None,
    device: str = "cpu",
    selection: Path | None = None,
    analyzers: dict[str, Analyzer] | None = None,
) -> dict:
    from filelock import FileLock

    from pipelines.image_distribution import validate_image_bundle

    if analyzers is None:
        analyzers = {}
        if ocr_model is not None:
            from pipelines.ocr import Analyzer as OCR

            analyzers["ocr"] = OCR.from_manifest(ocr_model, device="cpu")
        if description_model is not None:
            from pipelines.description import Analyzer as Description

            analyzers["description"] = Description.from_manifest(
                description_model, device=device
            )
    if not analyzers or set(analyzers) - {"ocr", "description"}:
        raise ValueError("Select at least one OCR or description model")
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format_version") not in {3, 4, 5} or "image" not in manifest:
            raise ValueError("Observation analysis requires an image bundle")
        entities = [
            json.loads(
                archive.read("entities/" + row["id"].replace(":", "/") + ".json")
            )
            for row in json.loads(archive.read("index.json"))
        ]
        validate_image_bundle(archive, manifest, entities)
        images = json.loads(archive.read("image/index.json"))
    indexed = [row for row in images if row["vector_index"] is not None]
    chosen = None
    if selection is not None:
        value = json.loads(selection.read_bytes())
        ids = value.get("media_ids")
        if (
            value.get("schema_version") != 1
            or not isinstance(ids, list)
            or any(not isinstance(item, str) for item in ids)
            or len(ids) != len(set(ids))
            or not set(ids).issubset({row["id"] for row in indexed})
        ):
            raise ValueError("Observation selection requires indexed media IDs")
        chosen = set(ids)
    grouped: dict[str, list[dict]] = {}
    outcomes: list[dict] = []
    for row in indexed:
        if chosen is not None and row["id"] not in chosen:
            outcomes.extend(
                {"media_id": row["id"], "kind": kind, "state": "selection"}
                for kind in sorted(analyzers)
            )
        else:
            grouped.setdefault(row["sha256"], []).append(row)
    recipes, recipe_ids = {}, {}
    for kind, analyzer in sorted(analyzers.items()):
        recipe = asdict(analyzer.recipe)
        validate_recipe(recipe)
        recipe_ids[kind] = digest(canonical(recipe))
        recipes[recipe_ids[kind]] = recipe
    observations = []
    output.parent.mkdir(parents=True, exist_ok=True)
    with FileLock(output.with_suffix(".lock"), timeout=0), MediaStore(root) as store:
        for position, (sha, media) in enumerate(sorted(grouped.items()), 1):
            for kind, analyzer in sorted(analyzers.items()):
                try:

                    def produce(source: Path, destination: Path) -> None:
                        result = analyzer.analyze(source.read_bytes())
                        validate_output(result, kind, allow_empty=True)
                        destination.write_bytes(canonical(result))

                    artifact = store.derive(sha, analyzer.recipe, produce)
                    result = json.loads(artifact.read_bytes())
                    validate_output(result, kind, allow_empty=True)
                    if not result["text"].strip():
                        outcomes.extend(
                            {"media_id": row["id"], "kind": kind, "state": "empty"}
                            for row in media
                        )
                        continue
                    observation = {
                        **result,
                        "kind": kind,
                        "origin": "generated",
                        "media_sha256": sha,
                        "media_ids": sorted(row["id"] for row in media),
                        "references": image_references(media),
                        "recipe_sha256": recipe_ids[kind],
                    }
                    observation["id"] = observation_id(observation)
                    observations.append(observation)
                    outcomes.extend(
                        {
                            "media_id": row["id"],
                            "kind": kind,
                            "state": "observed",
                            "observation_id": observation["id"],
                        }
                        for row in media
                    )
                except (ValueError, OSError, RuntimeError) as error:
                    print(
                        f"Observation {kind} failed for {media[0]['id']}: {error}",
                        file=sys.stderr,
                        flush=True,
                    )
                    outcomes.extend(
                        {
                            "media_id": row["id"],
                            "kind": kind,
                            "state": "failed",
                            "reason": type(error).__name__,
                        }
                        for row in media
                    )
            if position % 10 == 0 or position == len(grouped):
                print(
                    f"Observed {position}/{len(grouped)} unique images (cached or new)",
                    file=sys.stderr,
                    flush=True,
                )
        value = {
            "schema_version": 1,
            "gallery_sha256": manifest["image"]["gallery_sha256"],
            "kinds": sorted(analyzers),
            "recipes": recipes,
            "observations": sorted(observations, key=lambda row: row["id"]),
            "outcomes": sorted(
                outcomes, key=lambda row: (row["media_id"], row["kind"])
            ),
        }
        validate_analysis(value, manifest, images)
        temporary = output.with_suffix(".tmp")
        temporary.write_bytes(canonical(value))
        temporary.replace(output)
    return {
        "output": str(output),
        "observations": len(observations),
        "images": len(grouped),
        "states": dict(sorted(Counter(row["state"] for row in outcomes).items())),
        "analysis_sha256": digest(canonical(value)),
    }
