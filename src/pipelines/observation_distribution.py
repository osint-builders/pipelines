"""Package generated image text in an independent, validated search index."""

import json
import math
import struct
import zipfile
from collections import Counter
from pathlib import Path

from pipelines.distribution import LOCK, Encoder
from pipelines.observations import (
    MAX_OBSERVATIONS,
    canonical,
    digest,
    validate_analysis,
    validate_recipe,
    validate_row,
)

MAX_CHUNKS = 100_000
MEMBER_LIMITS = {
    "observations/index.json": 64 << 20,
    "observations/recipes.json": 8 << 20,
    "observations/chunks.json": 64 << 20,
    "observations/vectors.f32": 384 * 4 * MAX_CHUNKS,
    "observations/report.json": 16 << 20,
    "observations/probes.json": 1 << 20,
}
MEMBERS = set(MEMBER_LIMITS)
SEARCH = {"aggregation": "max-v1", "calibration": None}


def validate_vector(body: bytes, dimensions: int) -> None:
    if len(body) != dimensions * 4:
        raise ValueError("Observation embedding dimension mismatch")
    values = struct.unpack(f"<{dimensions}f", body)
    if (
        any(not math.isfinite(value) for value in values)
        or abs(sum(value * value for value in values) - 1) > 0.002
    ):
        raise ValueError("Observation embedding must be finite and normalized")


def text_chunks(encoder: Encoder, text: str) -> list[str]:
    tokens = encoder.tokenizer.encode(text, add_special_tokens=False)
    chunks: list[str] = []
    for start in range(0, len(tokens.ids), 144):
        end = min(start + 192, len(tokens.ids))
        piece = text[tokens.offsets[start][0] : tokens.offsets[end - 1][1]]
        if (
            not piece.strip()
            or len(encoder.tokenizer.encode(piece).ids) > LOCK["max_tokens"]
        ):
            raise ValueError("Observation chunk exceeds the text model token limit")
        chunks.append(piece)
        if end == len(tokens.ids):
            break
    if not chunks:
        raise ValueError("Observation text has no searchable tokens")
    return chunks


def build_observation_members(
    analysis_path: Path,
    image_members: dict[str, bytes],
    image_metadata: dict,
    model: Path,
    cache: Path,
) -> tuple[dict[str, bytes], dict]:
    analysis = json.loads(analysis_path.read_bytes())
    images = json.loads(image_members["image/index.json"])
    validate_analysis(analysis, {"image": image_metadata}, images)
    encoder = Encoder(model)
    chunks: list[dict] = []
    for row in analysis["observations"]:
        for piece in text_chunks(encoder, row["text"]):
            chunks.append(
                {
                    "observation_id": row["id"],
                    "text": piece,
                    "vector_index": len(chunks),
                }
            )
    if len(chunks) > MAX_CHUNKS:
        raise ValueError("Observation chunk budget exceeded")
    cache.mkdir(parents=True, exist_ok=True)
    model_key = digest(canonical(LOCK))
    keys = [digest(model_key.encode() + row["text"].encode()) for row in chunks]
    missing = list(
        dict.fromkeys(key for key in keys if not (cache / f"{key}.f32").exists())
    )
    texts = {key: row["text"] for key, row in zip(keys, chunks, strict=True)}
    width = LOCK["dimensions"] * 4
    for start in range(0, len(missing), 16):
        batch = missing[start : start + 16]
        raw = encoder.encode([texts[key] for key in batch])
        if len(raw) != len(batch) * width:
            raise ValueError("Observation encoder returned an invalid batch")
        for position, key in enumerate(batch):
            body = raw[position * width : (position + 1) * width]
            validate_vector(body, LOCK["dimensions"])
            path = cache / f"{key}.f32"
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(body)
            temporary.replace(path)
    vectors = []
    for key in keys:
        body = (cache / f"{key}.f32").read_bytes()
        validate_vector(body, LOCK["dimensions"])
        vectors.append(body)
    probes = [
        "yellow helicopter with landing skids",
        "radar panel on a tracked carrier",
        "SALVATAGGIO RESCUE",
    ]
    raw_probes = encoder.encode(probes)
    if len(raw_probes) != len(probes) * width:
        raise ValueError("Observation probes have invalid dimensions")
    reference = []
    for position, text in enumerate(probes):
        body = raw_probes[position * width : (position + 1) * width]
        validate_vector(body, LOCK["dimensions"])
        reference.append(
            {
                "text": text,
                "vector": list(struct.unpack(f"<{LOCK['dimensions']}f", body)),
            }
        )
    report = {
        "schema_version": 1,
        "gallery_sha256": image_metadata["gallery_sha256"],
        "kinds": analysis["kinds"],
        "outcomes": analysis["outcomes"],
        "counts": dict(
            sorted(Counter(row["state"] for row in analysis["outcomes"]).items())
        ),
    }
    members = {
        "observations/index.json": canonical(analysis["observations"]),
        "observations/recipes.json": canonical(analysis["recipes"]),
        "observations/chunks.json": canonical(chunks),
        "observations/vectors.f32": b"".join(vectors),
        "observations/report.json": canonical(report),
        "observations/probes.json": canonical(reference),
    }
    metadata = {
        "schema_version": 1,
        "records": len(analysis["observations"]),
        "chunks": len(chunks),
        "dimensions": LOCK["dimensions"],
        "vector_dtype": "float32-le",
        "embedding_model_sha256": LOCK["files"]["model.onnx"]["sha256"],
        "index_sha256": digest(members["observations/index.json"]),
        "gallery_sha256": image_metadata["gallery_sha256"],
        "search": SEARCH,
    }
    return members, metadata


def validate_observation_bundle(archive: zipfile.ZipFile, manifest: dict) -> dict:
    metadata = manifest.get("observations", {})
    if (
        manifest.get("format_version") not in {4, 5}
        or "image" not in manifest
        or metadata.get("schema_version") != 1
        or metadata.get("dimensions") != LOCK["dimensions"]
        or metadata.get("vector_dtype") != "float32-le"
        or metadata.get("embedding_model_sha256")
        != LOCK["files"]["model.onnx"]["sha256"]
        or metadata.get("embedding_model_sha256")
        != manifest["files"].get("model/model.onnx")
        or metadata.get("gallery_sha256") != manifest["image"]["gallery_sha256"]
        or type(metadata.get("records")) is not int
        or not 0 <= metadata["records"] <= MAX_OBSERVATIONS
        or type(metadata.get("chunks")) is not int
        or not 0 <= metadata["chunks"] <= MAX_CHUNKS
        or metadata.get("search") != SEARCH
    ):
        raise ValueError("Invalid observation extension contract")
    actual = [name for name in archive.namelist() if name.startswith("observations/")]
    declared = {name for name in manifest["files"] if name.startswith("observations/")}
    if len(actual) != len(set(actual)) or set(actual) != MEMBERS or declared != MEMBERS:
        raise ValueError("Missing, duplicate or undeclared observation members")
    for name in MEMBERS:
        if archive.getinfo(name).file_size > MEMBER_LIMITS[name]:
            raise ValueError("Observation artifact is oversized")
        if digest(archive.read(name)) != manifest["files"][name]:
            raise ValueError("Observation artifact checksum mismatch")
    raw = archive.read("observations/index.json")
    if digest(raw) != metadata.get("index_sha256"):
        raise ValueError("Observation index checksum mismatch")
    rows = json.loads(raw)
    recipes = json.loads(archive.read("observations/recipes.json"))
    if not isinstance(recipes, dict) or not recipes:
        raise ValueError("Observation recipes are missing")
    for identity, recipe in recipes.items():
        validate_recipe(recipe)
        if digest(canonical(recipe)) != identity:
            raise ValueError("Observation recipe checksum mismatch")
    images = {row["id"]: row for row in json.loads(archive.read("image/index.json"))}
    if len(rows) != metadata["records"] or [row["id"] for row in rows] != sorted(
        {row["id"] for row in rows}
    ):
        raise ValueError("Observation count or order mismatch")
    for row in rows:
        validate_row(row, recipes, images)
    chunks = json.loads(archive.read("observations/chunks.json"))
    vectors = archive.read("observations/vectors.f32")
    width = metadata["dimensions"] * 4
    if len(chunks) != metadata["chunks"] or len(vectors) != len(chunks) * width:
        raise ValueError("Observation chunk/vector count mismatch")
    by_id = {row["id"]: row for row in rows}
    used: set[str] = set()
    previous = ""
    for position, chunk in enumerate(chunks):
        row = by_id.get(chunk.get("observation_id"))
        if (
            set(chunk) != {"observation_id", "text", "vector_index"}
            or row is None
            or row["id"] < previous
            or type(chunk["vector_index"]) is not int
            or chunk["vector_index"] != position
            or not isinstance(chunk["text"], str)
            or not chunk["text"].strip()
            or chunk["text"] not in row["text"]
        ):
            raise ValueError("Invalid observation text chunk")
        used.add(row["id"])
        previous = row["id"]
        validate_vector(
            vectors[position * width : (position + 1) * width], metadata["dimensions"]
        )
    if used != set(by_id):
        raise ValueError("Observation has no searchable text chunk")
    probes = json.loads(archive.read("observations/probes.json"))
    if not isinstance(probes, list) or not 3 <= len(probes) <= 100:
        raise ValueError("Invalid observation probe count")
    seen_probes: set[str] = set()
    for probe in probes:
        if (
            not isinstance(probe.get("text"), str)
            or not probe["text"].strip()
            or len(probe["text"]) > 1000
            or probe["text"] in seen_probes
        ):
            raise ValueError("Invalid observation probe text")
        seen_probes.add(probe["text"])
        vector = probe.get("vector")
        if (
            not isinstance(vector, list)
            or len(vector) != metadata["dimensions"]
            or any(type(v) not in {float, int} for v in vector)
        ):
            raise ValueError("Invalid observation probe dimensions")
        validate_vector(
            struct.pack(f"<{len(vector)}f", *vector), metadata["dimensions"]
        )
    report = json.loads(archive.read("observations/report.json"))
    validate_analysis(
        {**report, "recipes": recipes, "observations": rows},
        manifest,
        list(images.values()),
    )
    if report.get("counts") != dict(
        Counter(item["state"] for item in report["outcomes"])
    ):
        raise ValueError("Observation analysis outcome counts mismatch")
    return metadata
