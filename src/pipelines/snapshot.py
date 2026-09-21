import json
import re
from pathlib import Path

from pipelines.model import SCHEMA_VERSION, EntityKind, evidence_id, valid_key


def load_snapshot(source_dir: Path) -> tuple[dict, list[dict]]:
    manifest = json.loads((source_dir / "published/current.json").read_text())
    if manifest["schema_version"] != SCHEMA_VERSION:
        raise ValueError(
            "Old snapshot format; run pipeline-build extract on its saved archive"
        )
    if manifest["source"] != source_dir.name:
        raise ValueError("Snapshot source mismatch")
    for key in ("snapshot", "archive"):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", manifest[key]):
            raise ValueError(f"Invalid {key} ID")
    snapshot = source_dir / "published/snapshots" / manifest["snapshot"]
    entities = []
    for line in (snapshot / "entities.jsonl").read_text(encoding="utf-8").splitlines():
        entity = json.loads(line)
        key = entity["source_id"]
        if (
            not valid_key(key)
            or entity["id"] != f"{source_dir.name}:{key}"
            or entity["source"] != source_dir.name
        ):
            raise ValueError("Invalid source-qualified entity ID")
        EntityKind(entity["kind"])
        if not entity["title"].strip() or not entity["evidence"]:
            raise ValueError("Entity requires title and evidence")
        seen: set[str] = set()
        for page in entity["evidence"]:
            record_id = page.get("record_id", "")
            if record_id and (
                not isinstance(record_id, str)
                or not valid_key(record_id)
                or not page.get("records")
            ):
                raise ValueError("Invalid record evidence")
            if page["id"] != evidence_id(page["url"], record_id) or page["id"] in seen:
                raise ValueError("Invalid or duplicate evidence ID")
            seen.add(page["id"])
            page["markdown"] = (snapshot / "markdown" / f"{page['id']}.md").read_text(
                encoding="utf-8"
            )
        entities.append(entity)
    if (
        not entities
        or len(entities) != manifest["entities"]
        or len({item["id"] for item in entities}) != len(entities)
    ):
        raise ValueError("Entity count mismatch or duplicate IDs")
    return manifest, entities


def status(source_dir: Path) -> dict:
    result: dict = {"published": None, "work": None}
    current = source_dir / "published/current.json"
    if current.is_file():
        result["published"] = json.loads(current.read_text())
        if (source_dir.parent / "media/manifest.sqlite").is_file() or "media" in result[
            "published"
        ]:
            from pipelines.media_pipeline import media_summary, read_media

            media = read_media(
                source_dir.name, source_dir.parent, result["published"]["archive"]
            )
            if media is not None:
                result["media"] = media_summary(media)
    active = source_dir / "work.json"
    if active.is_file():
        work = json.loads(active.read_text())
        if not re.fullmatch(r"[A-Za-z0-9_-]+", work["archive"]):
            raise ValueError("Invalid archive ID")
        progress = source_dir / "archives" / work["archive"] / "progress.json"
        if progress.is_file():
            work["progress"] = json.loads(progress.read_text())
        result["work"] = work
    return result
