import json
import re
import tempfile
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path

from filelock import FileLock

from pipelines.archive import Archive, atomic_json
from pipelines.media import (
    MAX_IMAGE_BYTES,
    MEDIA_SCHEMA_VERSION,
    MediaCandidate,
    MediaStore,
    media_id,
)
from pipelines.snapshot import load_snapshot
from pipelines.sources.base import MediaSource, PreparedSource, Source


def _scope(source: str, archive_id: str) -> None:
    if not re.fullmatch(r"[a-z0-9_-]+", source):
        raise ValueError("Invalid media source ID")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", archive_id):
        raise ValueError("Invalid media archive ID")


def _references(report: dict, entities: list[dict]) -> None:
    known = {
        (entity["id"], page["id"]) for entity in entities for page in entity["evidence"]
    }
    for record in report["records"]:
        for reference in record["references"]:
            pair = (reference["entity_id"], reference["evidence_id"])
            if pair not in known:
                raise ValueError("Media reference is absent from the entity snapshot")


def read_media(
    source: str,
    root: Path,
    archive_id: str,
    *,
    entities: list[dict] | None = None,
    verify: bool = False,
) -> dict | None:
    """Read media for one archive without creating storage or requesting URLs."""
    _scope(source, archive_id)
    checkpoint = root / source / "archives" / archive_id / "media.json"
    if checkpoint.is_file():
        saved = json.loads(checkpoint.read_text(encoding="utf-8"))
        if (
            saved.get("schema_version") != MEDIA_SCHEMA_VERSION
            or saved.get("source") != source
            or saved.get("archive") != archive_id
        ):
            raise ValueError("Invalid media checkpoint scope or version")
    if not (root / "media" / "manifest.sqlite").is_file():
        if checkpoint.is_file():
            raise ValueError("Media checkpoint exists but its archive is missing")
        return None
    with MediaStore(root, read_only=True) as store:
        report = store.manifest(source, archive_id)
        if not report["records"] and not checkpoint.is_file():
            return None
        if entities is not None:
            _references(report, entities)
        if verify:
            for digest in {
                record["sha256"]
                for record in report["records"]
                if record["state"] == "saved"
            }:
                store.body(digest)
    if entities is not None:
        report["coverage"] = media_coverage(report, entities)
    return report


def media_coverage(report: dict, entities: list[dict]) -> dict:
    """Count URL outcomes and original groups separately for every entity."""
    by_entity: dict[str, list[dict]] = defaultdict(list)
    for record in report["records"]:
        for identity in {ref["entity_id"] for ref in record["references"]}:
            by_entity[identity].append(record)
    rows = []
    for entity in sorted(entities, key=lambda item: item["id"]):
        records = by_entity[entity["id"]]
        counts = {
            state: 0
            for state in ("pending", "saved", "failed", "excluded", "unassociated")
        }
        groups: set[str] = set()
        for record in records:
            occurrences = [
                item
                for item in record.get("occurrences", [])
                if any(
                    ref["entity_id"] == entity["id"]
                    for ref in item.get("references", record["references"])
                )
            ]
            eligible = [
                item
                for item in occurrences
                if item["associated"] and not item["exclusion_reason"]
            ]
            state = record["state"]
            if record.get("occurrences") and not eligible:
                state = "excluded"
            counts[state] += 1
            if state == "saved":
                groups.update(item["original_url"] for item in eligible)
                if not record.get("occurrences"):
                    groups.add(record["url"])
        outcome = (
            "incomplete"
            if counts["pending"] or counts["failed"]
            else "saved"
            if counts["saved"]
            else "no_applicable_images"
        )
        rows.append(
            {
                "entity_id": entity["id"],
                "outcome": outcome,
                "reason": ("no_candidates" if not records else "no_eligible_candidates")
                if outcome == "no_applicable_images"
                else "",
                "discovered": len(records),
                **counts,
                "saved_original_groups": len(groups),
            }
        )
    return {
        "entities": len(entities),
        "with_candidates": sum(bool(row["discovered"]) for row in rows),
        "with_saved_media": sum(bool(row["saved"]) for row in rows),
        "without_candidates": [
            row["entity_id"] for row in rows if not row["discovered"]
        ],
        "multiple_subject_records": sum(
            len({ref["entity_id"] for ref in record["references"]}) > 1
            for record in report["records"]
        ),
        "outcomes": dict(sorted(Counter(row["outcome"] for row in rows).items())),
        "quality": _media_quality(report),
        "by_entity": rows,
    }


def _media_quality(report: dict) -> dict:
    saved = [row for row in report["records"] if row["state"] == "saved"]
    dimensions = [
        (row["width"], row["height"])
        for row in saved
        if isinstance(row.get("width"), int) and isinstance(row.get("height"), int)
    ]
    roles: Counter[str] = Counter()
    captioned = 0
    for row in saved:
        eligible = [
            item
            for item in row.get("occurrences", [])
            if item["associated"] and not item["exclusion_reason"]
        ]
        roles.update({item.get("role", "original") for item in eligible})
        captioned += any(item.get("caption", "").strip() for item in eligible)
    return {
        "saved_records": len(saved),
        "captioned_saved_records": captioned,
        "saved_records_by_role": dict(sorted(roles.items())),
        "unique_contents": len({row["sha256"] for row in saved if row.get("sha256")}),
        "exact_duplicate_groups": len(report.get("exact_duplicates", [])),
        "near_duplicate_pairs": len(report.get("near_duplicates", [])),
        "resolution": {
            "measured_records": len(dimensions),
            "min_width": min((width for width, _ in dimensions), default=None),
            "max_width": max((width for width, _ in dimensions), default=None),
            "min_height": min((height for _, height in dimensions), default=None),
            "max_height": max((height for _, height in dimensions), default=None),
            "short_edge_below_224": sum(min(size) < 224 for size in dimensions),
        },
        "failed_records_by_reason": dict(
            sorted(
                Counter(
                    row.get("error") or "unspecified"
                    for row in report["records"]
                    if row["state"] == "failed"
                ).items()
            )
        ),
        "excluded_occurrences_by_reason": dict(
            sorted(
                Counter(
                    item["exclusion_reason"]
                    for row in report["records"]
                    for item in row.get("occurrences", [])
                    if item["exclusion_reason"]
                ).items()
            )
        ),
    }


def media_summary(report: dict) -> dict:
    counts = report["counts"]
    return {
        "schema_version": report["schema_version"],
        "counts": counts,
        "complete": not (counts.get("pending", 0) or counts.get("failed", 0)),
    }


def audit_media(
    source: str, root: Path, manifest: dict, entities: list[dict]
) -> dict | None:
    report = read_media(
        source, root, manifest["archive"], entities=entities, verify=True
    )
    if "media" in manifest:
        if manifest["media"] != {
            "schema_version": MEDIA_SCHEMA_VERSION,
            "file": "media.json",
        }:
            raise ValueError("Unsupported published media descriptor")
        path = (
            root / source / "published/snapshots" / manifest["snapshot"] / "media.json"
        )
        snapshot = json.loads(path.read_text(encoding="utf-8"))
        if (
            report is None
            or snapshot.get("schema_version") != MEDIA_SCHEMA_VERSION
            or snapshot.get("source") != source
            or snapshot.get("archive") != manifest["archive"]
        ):
            raise ValueError("Published media archive is missing or mismatched")
        _references(snapshot, entities)
        current = {row["id"]: row for row in report["records"]}
        for record in snapshot["records"]:
            archived = current.get(record["id"], {})
            if record["url"] != archived.get("url") or (
                record["state"] == "saved"
                and record["sha256"] != archived.get("sha256")
            ):
                raise ValueError("Published media differs from its archived capture")
    return media_summary(report) if report is not None else None


def _discover(source: Source, root: Path, manifest: dict, entities: list[dict]) -> None:
    if not isinstance(source, MediaSource):
        raise ValueError("Source has no media discovery capability")
    archive_id = manifest["archive"]
    path = root / source.id / "archives" / archive_id
    if not (path / "manifest.sqlite").is_file():
        raise FileNotFoundError("Source archive is missing")
    complete = path / "complete.json"
    if (
        not complete.is_file()
        or json.loads(complete.read_text(encoding="utf-8"))["source"] != source.id
    ):
        raise ValueError("Media discovery requires a completed source archive")
    owners: dict[str, list[dict]] = defaultdict(list)
    for entity in entities:
        for url in {page["url"] for page in entity["evidence"]}:
            owners[url].append(entity)
    candidates: list[MediaCandidate] = []
    with closing(Archive(path)) as archive:
        if archive.pages("pending", "failed"):
            raise ValueError("Media discovery requires a completed source archive")
        pages = archive.pages("saved")
        if isinstance(source, PreparedSource):
            source.prepare((page["url"], archive.body(page)) for page in pages)
        for page in pages:
            url = page["url"]
            known = {
                (entity["id"], evidence["id"])
                for entity in owners[url]
                for evidence in entity["evidence"]
                if evidence["url"] == url
            }
            for candidate in source.discover_media(
                url, archive.body(page), owners[url]
            ):
                if candidate.page_url and candidate.page_url != url:
                    raise ValueError(
                        "Media occurrence must belong to its archived page"
                    )
                if any(
                    (reference.entity_id, reference.evidence_id) not in known
                    for reference in candidate.references
                ):
                    raise ValueError("Media reference must belong to its archived page")
                if candidate.embedded_body is not None and (
                    candidate.role != "preview"
                    or candidate.original_url != url
                    or candidate.url != url
                    or not candidate.references
                    or candidate.exclusion_reason
                    or not candidate.embedded_content_type.startswith("image/")
                    or not candidate.embedded_body
                    or len(candidate.embedded_body) > MAX_IMAGE_BYTES
                ):
                    raise ValueError("Invalid embedded image preview")
                candidates.append(candidate)
    with MediaStore(root) as store:
        store.register(source.id, archive_id, candidates)
        for candidate in candidates:
            if candidate.embedded_body is None:
                continue
            with tempfile.TemporaryDirectory(prefix="pipeline-embedded-") as temporary:
                path = Path(temporary) / "image"
                path.write_bytes(candidate.embedded_body)
                store.save(
                    source.id,
                    archive_id,
                    media_id(source.id, candidate.url),
                    path,
                    content_type=candidate.embedded_content_type,
                )


def media(source: Source, root: Path, *, download: bool = False) -> dict:
    """Discover saved-page media; download originals only when explicitly requested."""
    _scope(source.id, "scope-check")
    if not isinstance(source, MediaSource):
        return {"source": source.id, "supported": False, "reason": "no_media_adapter"}
    source_dir = root / source.id
    if not (source_dir / "published/current.json").is_file():
        raise FileNotFoundError("Media discovery requires a published entity snapshot")
    with FileLock(source_dir / "writer.lock", timeout=0):
        manifest, entities = load_snapshot(source_dir)
        archive_id = manifest["archive"]
        _discover(source, root, manifest, entities)
        checkpoint = source_dir / "archives" / archive_id / "media.json"

        def project() -> dict:
            with MediaStore(root, read_only=True) as store:
                result = store.manifest(source.id, archive_id)
            _references(result, entities)
            result["coverage"] = media_coverage(result, entities)
            result.update(supported=True, **media_summary(result))
            atomic_json(checkpoint, result)
            return result

        result = project()
        if download:
            from pipelines.media_download import capture_media

            try:
                capture_media(source, root, archive_id)
            finally:
                result = project()
        return result
