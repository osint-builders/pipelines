"""Build and validate the optional offline image gallery extension."""

import json
import re
import sys
import zipfile
from collections import Counter, defaultdict
from dataclasses import asdict
from io import BytesIO
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from pipelines.distribution import canonical, sha256
from pipelines.media_context import eligible_reference

if TYPE_CHECKING:
    import numpy as np
    from numpy.typing import NDArray

    from pipelines.image_embedding import Encoder

MAX_VECTORS = 10_000
MAX_PREVIEW_BYTES = 32 * 1024 * 1024
MAX_VIEWS = 8
IMAGE_LOCK_PATH = Path(__file__).with_name("image_model.lock.json")
MODEL_KEYS = (
    "schema_version",
    "id",
    "variant",
    "checkpoint",
    "file",
    "sha256",
    "bytes",
    "input",
    "output",
    "shape",
    "dimensions",
    "normalization",
    "preprocess",
)


def _model(path: Path) -> tuple[dict, bytes, bytes]:
    lock = json.loads(IMAGE_LOCK_PATH.read_bytes())
    lock_body = canonical(lock)
    supplied = json.loads(path.read_text(encoding="utf-8"))
    if any(supplied.get(key) != lock[key] for key in MODEL_KEYS):
        raise ValueError("Image model does not match the pinned model contract")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+\.onnx", lock["file"]):
        raise ValueError("Invalid image model filename")
    body = (path.parent / lock["file"]).read_bytes()
    if len(body) != lock["bytes"] or sha256(body) != lock["sha256"]:
        raise ValueError("Image model checksum mismatch")
    return lock, lock_body, body


def _selection(path: Path | None) -> set[str] | None:
    if path is None:
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    ids = value.get("media_ids")
    if (
        value.get("schema_version") != 1
        or not isinstance(ids, list)
        or any(not isinstance(item, str) for item in ids)
        or len(set(ids)) != len(ids)
    ):
        raise ValueError("Invalid explicit image selection")
    return set(ids)


def _references(
    record: dict, owners: dict[str, set[str]]
) -> tuple[list[dict], list[dict]]:
    references = []
    for item in record["references"]:
        entity, evidence = item["entity_id"], item["evidence_id"]
        if (
            entity not in owners
            or evidence not in owners[entity]
            or entity.split(":", 1)[0] != record["source"]
        ):
            raise ValueError(
                "Image reference is absent from its source entity evidence"
            )
        references.append(
            {
                "entity_id": entity,
                "evidence_id": evidence,
                "caption": item.get("caption", ""),
                "section": item.get("section", ""),
                "association": item.get("association", "source_context"),
                "ambiguous": item.get("ambiguous", False),
            }
        )
    references = sorted(
        {canonical(item): item for item in references}.values(), key=canonical
    )
    occurrences = record.get("occurrences", [])
    if not occurrences:
        return references, []
    active: list[dict] = []
    excluded: list[dict] = []
    for item in references:
        (active if eligible_reference(record, item) else excluded).append(item)
    return active, excluded


def _row(record: dict, references: list[dict]) -> dict:
    occurrences = record.get("occurrences", [])
    eligible = [
        item
        for item in occurrences
        if item.get("associated") and not item.get("exclusion_reason")
    ]
    context = sorted(eligible or occurrences, key=canonical)
    original_urls = sorted(
        {item.get("original_url", "") for item in context if item.get("original_url")}
    )
    return {
        "id": record["id"],
        "source": record["source"],
        "url": record["url"],
        "original_url": original_urls[0] if original_urls else "",
        "sha256": record["sha256"],
        "content_type": record["content_type"],
        "width": record["width"],
        "height": record["height"],
        "caption": next(
            (item.get("caption", "") for item in context if item.get("caption")),
            references[0]["caption"] if references else "",
        ),
        "references": references,
        "vector_index": None,
        "exclusion_reason": "",
        "preview": None,
    }


def _unit(body: bytes, dimensions: int, dtype: str = "<f2") -> "NDArray[np.float32]":
    import numpy as np

    width = np.dtype(dtype).itemsize
    if len(body) != dimensions * width:
        raise ValueError("Image vector cache has an invalid size")
    vector = np.frombuffer(body, dtype=dtype).astype(np.float32)
    squared = float(np.dot(vector.astype(np.float64), vector.astype(np.float64)))
    tolerance = 0.002 if dtype == "<f2" else 0.0001
    if not np.isfinite(vector).all() or abs(squared - 1) > tolerance:
        raise ValueError("Image vector must be finite and unit normalized")
    return vector / np.float32(squared**0.5)


def _families(records: list[dict]) -> list[list[dict]]:
    parents = {row["id"]: row["id"] for row in records}

    def find(key: str) -> str:
        while parents[key] != key:
            parents[key] = parents[parents[key]]
            key = parents[key]
        return key

    groups: dict[tuple[str, str], str] = {}
    for row in records:
        for original in row["_groups"]:
            key = (row["source"], original)
            if key in groups:
                a, b = find(row["id"]), find(groups[key])
                parents[max(a, b)] = min(a, b)
            else:
                groups[key] = row["id"]
    result: dict[str, list[dict]] = defaultdict(list)
    for row in records:
        result[find(row["id"])].append(row)
    return [result[key] for key in sorted(result)]


def _diverse(vectors: dict, owners: dict[str, set[str]]) -> set[str]:
    import numpy as np

    by_entity: dict[str, list[str]] = defaultdict(list)
    for digest in sorted(vectors):
        for entity in sorted(owners[digest]):
            by_entity[entity].append(digest)
    proposals = {}
    for entity, candidates in sorted(by_entity.items()):
        picked = [candidates[0]]
        nearest = {
            key: float(np.dot(vectors[key], vectors[picked[0]])) for key in candidates
        }
        while len(picked) < min(MAX_VIEWS, len(candidates)):
            chosen = min(
                (key for key in candidates if key not in picked),
                key=lambda key: (nearest[key], key),
            )
            picked.append(chosen)
            for key in candidates:
                nearest[key] = max(
                    nearest[key], float(np.dot(vectors[key], vectors[chosen]))
                )
        proposals[entity] = picked
    selected: set[str] = set()
    counts: Counter = Counter()
    for position in range(MAX_VIEWS):
        for entity in sorted(proposals):
            choices = proposals[entity]
            if position >= len(choices):
                continue
            digest = choices[position]
            if digest in selected or any(
                counts[owner] >= MAX_VIEWS for owner in owners[digest]
            ):
                continue
            if len(selected) >= MAX_VECTORS:
                break
            selected.add(digest)
            counts.update(owners[digest])
    return selected


def _preview(input_path: Path, output_path: Path) -> None:
    from PIL import Image

    from pipelines.image_preprocess import _decode

    image = _decode(input_path.read_bytes())
    image.thumbnail((512, 512), Image.Resampling.LANCZOS)
    image.save(
        output_path,
        format="JPEG",
        quality=75,
        subsampling=2,
        optimize=False,
        progressive=False,
    )


def _probes(encoder: "Encoder", dimensions: int) -> dict[str, bytes]:
    import numpy as np
    from PIL import Image

    members: dict[str, bytes] = {}
    vectors = []
    values = np.arange(11 * 7 * 3, dtype=np.uint32).reshape(7, 11, 3)
    pixels = ((values * 71) % 256).astype(np.uint8)
    for name, mode in [("rgb", "RGB"), ("orientation", "RGB"), ("alpha", "RGBA")]:
        image = Image.fromarray(pixels)
        options: dict = {}
        extension, format = "png", "PNG"
        if mode == "RGBA":
            alpha = np.tile(
                np.array([0, 1, 127, 128, 254, 255, 64, 192, 21, 234, 91], np.uint8),
                (7, 1),
            )
            image = Image.fromarray(np.concatenate([pixels, alpha[:, :, None]], axis=2))
        if name == "orientation":
            exif = Image.Exif()
            exif[274] = 6
            options = {"exif": exif, "quality": 91, "subsampling": 0}
            extension, format = "jpg", "JPEG"
        stream = BytesIO()
        image.save(stream, format=format, **options)
        body = stream.getvalue()
        member = f"image/probes/{name}.{extension}"
        members[member] = body
        vector = encoder.encode(body)
        _unit(vector.astype("<f4").tobytes(), dimensions, "<f4")
        vectors.append({"id": name, "image_member": member, "vector": vector.tolist()})
    members["image/probes.json"] = canonical(vectors)
    return members


def build_image_members(
    root: Path,
    sources: list[str],
    entities: list[dict],
    model_manifest: Path,
    selection: Path | None = None,
) -> tuple[dict[str, bytes], dict, dict]:
    import numpy as np
    from PIL import Image
    from PIL import __version__ as pillow_version

    from pipelines.image_embedding import Encoder
    from pipelines.image_preprocess import _decode
    from pipelines.media import MediaStore, ProcessingRecipe
    from pipelines.media_pipeline import read_media
    from pipelines.snapshot import load_snapshot

    lock, lock_body, model_body = _model(model_manifest)
    chosen = _selection(selection)
    owners = {
        entity["id"]: {page["id"] for page in entity["evidence"]} for entity in entities
    }
    captures, raw_records = [], []
    for source in sorted(set(sources)):
        snapshot, source_entities = load_snapshot(root / source)
        media = read_media(source, root, snapshot["archive"], entities=source_entities)
        captures.append(
            {
                "source": source,
                "counts": media["counts"] if media else {},
                "available": media is not None,
            }
        )
        if media:
            raw_records.extend(media["records"])
    raw_records.sort(key=lambda row: row["id"])
    if chosen is not None and not chosen.issubset({row["id"] for row in raw_records}):
        raise ValueError("Explicit image selection contains unknown media IDs")
    rows, associations, outcomes, excluded_occurrences = [], [], [], []
    capture_identity = []
    for record in raw_records:
        refs, removed = _references(record, owners)
        capture_identity.append(
            {
                key: record.get(key)
                for key in (
                    "id",
                    "source",
                    "url",
                    "state",
                    "sha256",
                    "content_type",
                    "width",
                    "height",
                    "references",
                    "occurrences",
                    "error",
                    "final_url",
                    "http_status",
                )
            }
        )
        if removed:
            associations.append(
                {
                    "id": record["id"],
                    "references": removed,
                    "reason": "excluded_occurrence",
                }
            )
        excluded_context = [
            item
            for item in record.get("occurrences", [])
            if item.get("exclusion_reason") or not item.get("associated")
        ]
        if excluded_context:
            excluded_occurrences.append(
                {"id": record["id"], "occurrences": excluded_context}
            )
        if record["state"] != "saved":
            outcomes.append(
                {
                    "id": record["id"],
                    "source": record["source"],
                    "state": record["state"],
                    "error": record.get("error", ""),
                }
            )
            continue
        row = _row(record, refs)
        eligible = [
            item
            for item in record.get("occurrences", [])
            if item.get("associated") and not item.get("exclusion_reason")
        ]
        row["_groups"] = sorted({item["original_url"] for item in eligible}) or [
            row["url"]
        ]
        row["_original"] = not eligible or any(
            item["role"] == "original" for item in eligible
        )
        if chosen is not None and row["id"] not in chosen:
            row["exclusion_reason"] = "selection"
        elif not refs:
            row["exclusion_reason"] = "unassociated"
        elif row["content_type"] not in {"image/jpeg", "image/png"}:
            row["exclusion_reason"] = "unsupported_image_format"
        rows.append(row)
    encoder = Encoder.from_manifest(model_manifest)
    vector_recipe = ProcessingRecipe(
        version="image-vector-f16-v1",
        model_id=lock["id"],
        model_revision=lock["sha256"],
        settings={"model": lock, "dtype": "float16-le"},
    )
    preview_recipe = ProcessingRecipe(
        version="exif-white-fullview-jpeg-v1",
        settings={
            "max_edge": 512,
            "quality": 75,
            "subsampling": 2,
            "resampling": "lanczos",
            "pillow": pillow_version,
        },
    )
    vectors: dict[str, NDArray[np.float32]] = {}
    owners_by_hash: dict[str, set[str]] = defaultdict(set)
    encoded: dict[str, bytes] = {}
    members = {"image/model.json": lock_body, "image/" + lock["file"]: model_body}
    candidate_count = 0
    with MediaStore(root) as store:
        for family in _families([row for row in rows if not row["exclusion_reason"]]):
            preferred = sorted(
                family,
                key=lambda row: (
                    not row["_original"],
                    -row["width"] * row["height"],
                    row["id"],
                ),
            )
            winner = None
            for row in preferred:
                body = store.body(row["sha256"])
                try:
                    image = _decode(body)
                except ValueError:
                    row["exclusion_reason"] = "unsupported_image_encoding"
                    continue
                if image.size != (row["width"], row["height"]):
                    raise ValueError(
                        "Original image dimensions do not match the media archive"
                    )
                winner = row["sha256"]
                break
            for row in family:
                if not row["exclusion_reason"] and row["sha256"] != winner:
                    row["exclusion_reason"] = "alternate_resolution"
            if winner is None:
                continue
            family_references = {
                canonical(ref): ref for row in family for ref in row["references"]
            }
            for row in family:
                if not row["exclusion_reason"]:
                    row["references"] = [
                        family_references[key] for key in sorted(family_references)
                    ]
            if winner not in vectors:
                if len(vectors) >= MAX_VECTORS:
                    for row in family:
                        if not row["exclusion_reason"]:
                            row["exclusion_reason"] = "vector_budget"
                    continue

                def produce(input_path: Path, output_path: Path) -> None:
                    vector = encoder.encode(input_path.read_bytes())
                    _unit(vector.astype("<f4").tobytes(), lock["dimensions"], "<f4")
                    output_path.write_bytes(vector.astype("<f2").tobytes())

                cached = store.derive(winner, vector_recipe, produce).read_bytes()
                vectors[winner] = _unit(cached, lock["dimensions"])
                encoded[winner] = cached
                candidate_count += 1
                if candidate_count % 25 == 0:
                    print(
                        f"Prepared {candidate_count} image vectors (cached or new)",
                        file=sys.stderr,
                        flush=True,
                    )
            for row in family:
                if not row["exclusion_reason"]:
                    owners_by_hash[winner].update(
                        ref["entity_id"] for ref in row["references"]
                    )
        selected = _diverse(vectors, owners_by_hash)
        preview_bytes, previews = 0, {}
        for digest in sorted(selected):
            body = store.derive(digest, preview_recipe, _preview).read_bytes()
            preview_hash = sha256(body)
            member = f"image/previews/{preview_hash}.jpg"
            if member not in members and preview_bytes + len(body) > MAX_PREVIEW_BYTES:
                continue
            with Image.open(BytesIO(body)) as image:
                if image.format != "JPEG" or max(image.size) > 512:
                    raise ValueError("Invalid cached image preview")
                image.load()
                descriptor = {
                    "member": member,
                    "sha256": preview_hash,
                    "content_type": "image/jpeg",
                    "width": image.width,
                    "height": image.height,
                }
            if member not in members:
                members[member] = body
                preview_bytes += len(body)
            previews[digest] = descriptor
    indexed = sorted(previews)
    positions = {digest: index for index, digest in enumerate(indexed)}
    for row in rows:
        if not row["exclusion_reason"]:
            digest = row["sha256"]
            if digest not in selected:
                row["exclusion_reason"] = "view_budget"
            elif digest not in previews:
                row["exclusion_reason"] = "preview_budget"
            else:
                row.update(vector_index=positions[digest], preview=previews[digest])
        row.pop("_groups")
        row.pop("_original")
    members["image/index.json"] = canonical(rows)
    members["image/vectors.f16"] = b"".join(encoded[digest] for digest in indexed)
    members.update(_probes(encoder, lock["dimensions"]))
    report = {
        "schema_version": 1,
        "sources": captures,
        "capture_outcomes": outcomes,
        "association_exclusions": associations,
        "excluded_occurrences": excluded_occurrences,
        "capture_sha256": sha256(canonical(capture_identity)),
        "selection": sorted(chosen) if chosen is not None else None,
        "selection_method": "original-groups-farthest-first-round-robin-v1",
        "max_views_per_entity": MAX_VIEWS,
        "max_vectors": MAX_VECTORS,
        "max_preview_bytes": MAX_PREVIEW_BYTES,
        "vector_recipe": asdict(vector_recipe),
        "preview_recipe": asdict(preview_recipe),
        "outcomes": dict(
            sorted(
                Counter(row["exclusion_reason"] or "indexed" for row in rows).items()
            )
        ),
    }
    members["image/report.json"] = canonical(report)
    metadata = {
        "schema_version": 1,
        "model_sha256": lock["sha256"],
        "dimensions": lock["dimensions"],
        "vector_dtype": "float16-le",
        "vectors": len(indexed),
        "records": len(rows),
        "preview_bytes": preview_bytes,
        "gallery_sha256": sha256(members["image/index.json"]),
        "search": {"fusion": "rrf-v1", "rank_constant": 60, "calibration": None},
    }
    return members, metadata, report


def validate_image_bundle(
    archive: zipfile.ZipFile, manifest: dict, entities: list[dict]
) -> dict:
    import numpy as np
    from PIL import Image

    from pipelines.image_preprocess import MAX_IMAGE_BYTES, MAX_IMAGE_PIXELS, Recipe
    from pipelines.media import media_id

    if manifest.get("format_version") not in {3, 4}:
        raise ValueError("Image extension requires bundle format 3 or 4")
    metadata = manifest.get("image", {})
    lock = json.loads(IMAGE_LOCK_PATH.read_bytes())
    lock_body = canonical(lock)
    declared = {name for name in manifest["files"] if name.startswith("image/")}
    actual = [name for name in archive.namelist() if name.startswith("image/")]
    if set(actual) != declared or len(actual) != len(set(actual)):
        raise ValueError("Image bundle members are missing, duplicated or undeclared")
    for name in declared:
        if sha256(archive.read(name)) != manifest["files"][name]:
            raise ValueError("Image bundle checksum mismatch")
    if (
        archive.read("image/model.json") != lock_body
        or sha256(archive.read("image/" + lock["file"])) != lock["sha256"]
    ):
        raise ValueError("Image model does not match the pinned lock")
    Recipe(**lock["preprocess"]).validate()
    if (
        lock["shape"] != [1, 3, lock["preprocess"]["size"], lock["preprocess"]["size"]]
        or lock["normalization"] != "l2"
    ):
        raise ValueError("Invalid image model shape or normalization")
    if (
        metadata.get("schema_version") != 1
        or metadata.get("model_sha256") != lock["sha256"]
        or metadata.get("dimensions") != lock["dimensions"]
        or metadata.get("vector_dtype") != "float16-le"
        or metadata.get("search")
        != {"fusion": "rrf-v1", "rank_constant": 60, "calibration": None}
        or type(metadata.get("vectors")) is not int
        or not 0 <= metadata["vectors"] <= MAX_VECTORS
        or type(metadata.get("preview_bytes")) is not int
        or not 0 <= metadata["preview_bytes"] <= MAX_PREVIEW_BYTES
    ):
        raise ValueError("Invalid image extension contract")
    rows = json.loads(archive.read("image/index.json"))
    if (
        len(rows) != metadata.get("records")
        or [row["id"] for row in rows] != sorted({row["id"] for row in rows})
        or sha256(archive.read("image/index.json")) != metadata["gallery_sha256"]
    ):
        raise ValueError("Invalid image gallery identity or count")
    vectors = archive.read("image/vectors.f16")
    width = lock["dimensions"] * 2
    if len(vectors) != metadata["vectors"] * width:
        raise ValueError("Image vector count mismatch")
    for start in range(0, len(vectors), width):
        _unit(vectors[start : start + width], lock["dimensions"])
    owners = {
        entity["id"]: {page["id"] for page in entity["evidence"]} for entity in entities
    }
    previews: set[str] = set()
    used: dict[int, str] = {}
    hash_positions: dict[str, int] = {}
    entity_views: dict[str, set[int]] = defaultdict(set)
    for row in rows:
        if (
            row["id"] != media_id(row["source"], row["url"])
            or row["source"] not in manifest["sources"]
        ):
            raise ValueError("Invalid image record identity or source")
        if (
            not re.fullmatch(r"[a-f0-9]{64}", row["sha256"])
            or row["content_type"] not in {"image/jpeg", "image/png", "image/webp"}
            or type(row["width"]) is not int
            or type(row["height"]) is not int
            or min(row["width"], row["height"]) < 1
            or row["width"] * row["height"] > MAX_IMAGE_PIXELS
        ):
            raise ValueError("Invalid original image metadata")
        if row["original_url"] and urlsplit(row["original_url"]).scheme not in {
            "https",
            "http",
        }:
            raise ValueError("Invalid original image URL")
        refs, removed = _references({**row, "occurrences": []}, owners)
        if removed or refs != row["references"]:
            raise ValueError("Image references must be sorted and unique")
        position = row["vector_index"]
        if position is None:
            if not row["exclusion_reason"] or row["preview"] is not None:
                raise ValueError("Excluded image requires an explicit outcome")
            continue
        if (
            type(position) is not int
            or not 0 <= position < metadata["vectors"]
            or row["exclusion_reason"]
            or not refs
        ):
            raise ValueError("Invalid indexed image association")
        if (
            position in used
            and used[position] != row["sha256"]
            or row["sha256"] in hash_positions
            and hash_positions[row["sha256"]] != position
        ):
            raise ValueError("Image hash/vector ownership mismatch")
        used[position], hash_positions[row["sha256"]] = row["sha256"], position
        for ref in refs:
            entity_views[ref["entity_id"]].add(position)
        preview = row["preview"]
        if (
            not isinstance(preview, dict)
            or not re.fullmatch(
                r"image/previews/[a-f0-9]{64}\.(jpg|png)", preview.get("member", "")
            )
            or preview["sha256"] != Path(preview["member"]).stem
        ):
            raise ValueError("Indexed image requires a valid preview")
        body = archive.read(preview["member"])
        if len(body) > MAX_IMAGE_BYTES or sha256(body) != preview["sha256"]:
            raise ValueError("Image preview checksum mismatch")
        with Image.open(BytesIO(body)) as image:
            mime = {"JPEG": "image/jpeg", "PNG": "image/png"}.get(image.format or "")
            if (
                mime != preview["content_type"]
                or image.size != (preview["width"], preview["height"])
                or max(image.size) > 512
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Image preview decoding mismatch")
            image.load()
        previews.add(preview["member"])
    if set(used) != set(range(metadata["vectors"])) or any(
        len(values) > MAX_VIEWS for values in entity_views.values()
    ):
        raise ValueError("Orphan image vectors or excessive entity views")
    if sum(len(archive.read(name)) for name in previews) != metadata["preview_bytes"]:
        raise ValueError("Image preview budget mismatch")
    probes = json.loads(archive.read("image/probes.json"))
    if len(probes) < 3 or len({probe["id"] for probe in probes}) != len(probes):
        raise ValueError("Image bundle needs at least three unique probes")
    probe_members = set()
    for probe in probes:
        if not re.fullmatch(r"[a-z0-9_-]+", probe["id"]) or not re.fullmatch(
            r"image/probes/[a-z0-9_-]+\.(jpg|png)", probe["image_member"]
        ):
            raise ValueError("Invalid image parity probe identity")
        vector = np.asarray(probe["vector"], dtype=np.float32)
        if vector.shape != (lock["dimensions"],):
            raise ValueError("Image parity probe dimension mismatch")
        _unit(vector.astype("<f4").tobytes(), lock["dimensions"], "<f4")
        body = archive.read(probe["image_member"])
        if len(body) > MAX_IMAGE_BYTES:
            raise ValueError("Oversized image parity probe")
        with Image.open(BytesIO(body)) as image:
            if (
                image.format not in {"PNG", "JPEG"}
                or image.width * image.height > MAX_IMAGE_PIXELS
            ):
                raise ValueError("Invalid image parity probe body")
            image.load()
        probe_members.add(probe["image_member"])
    expected_members = (
        {
            "image/index.json",
            "image/vectors.f16",
            "image/model.json",
            "image/" + lock["file"],
            "image/probes.json",
            "image/report.json",
        }
        | previews
        | probe_members
    )
    if expected_members != declared:
        raise ValueError("Orphan image bundle artifacts")
    report = json.loads(archive.read("image/report.json"))
    if report.get("schema_version") != 1 or report.get("outcomes") != dict(
        Counter(row["exclusion_reason"] or "indexed" for row in rows)
    ):
        raise ValueError("Image outcome report mismatch")
    return {
        "records": len(rows),
        "vectors": len(used),
        "previews": len(previews),
        "preview_bytes": metadata["preview_bytes"],
        "probes": len(probes),
    }
