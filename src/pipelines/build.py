import json
import re
from pathlib import Path

from filelock import FileLock

from pipelines.archive import Archive, atomic_json, run_id
from pipelines.crawl import crawl
from pipelines.model import SCHEMA_VERSION, Entity
from pipelines.sources.base import Source


def publish(source: Source, archive: Archive, source_dir: Path) -> Path:
    if archive.pages("pending", "failed"):
        raise RuntimeError("An incomplete archive cannot be published")
    completion = archive.path / "complete.json"
    if (
        not completion.is_file()
        or json.loads(completion.read_text())["source"] != source.id
    ):
        raise RuntimeError(
            "Archive has no completion record; finish the explicit scrape first"
        )
    labels: dict[str, list[str]] = {}
    pages = archive.pages("saved")
    for page in pages:
        for url, names in source.labels(page["url"], archive.body(page)).items():
            labels.setdefault(url, []).extend(names)
    entities: dict[str, Entity] = {}
    excluded: list[str] = []
    for page in pages:
        headers = json.loads(page["headers"])
        if any(
            key.lower() == "x-robots-tag" and "noindex" in value.lower()
            for key, value in headers.items()
        ):
            excluded.append(page["url"])
            continue
        extracted = source.extract(
            page["url"], archive.body(page), labels.get(page["url"], [])
        )
        if not extracted:
            excluded.append(page["url"])
        for entity in extracted:
            if len(entity.evidence) != 1 or entity.evidence[0].url != page["url"]:
                raise ValueError(
                    "Adapter evidence must reference the current archived page"
                )
            evidence = entity.evidence[0]
            evidence.retrieved_at = page["fetched_at"]
            evidence.html_sha256 = page["sha256"]
            entity.validate()
            if entity.key in entities:
                entities[entity.key].merge(entity)
            else:
                entities[entity.key] = entity
    if len(entities) < source.minimum_entities:
        raise RuntimeError(
            f"Only {len(entities)} entities; expected at least {source.minimum_entities}"
        )
    published = source_dir / "published"
    published.mkdir(exist_ok=True)
    current = published / "current.json"
    if current.exists():
        previous = json.loads(current.read_text())
        if (
            previous["schema_version"] == SCHEMA_VERSION
            and len(entities) < previous["entities"] * 0.9
        ):
            raise RuntimeError(
                "Entity corpus shrank by more than 10%; refusing replacement"
            )
    snapshot_id = run_id()
    snapshot = published / "snapshots" / snapshot_id
    snapshot.mkdir(parents=True)
    markdown_dir = snapshot / "markdown"
    markdown_dir.mkdir()
    evidence_text: dict[str, str] = {}
    with (snapshot / "entities.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as catalog:
        for entity in sorted(entities.values(), key=lambda item: item.key):
            metadata = entity.metadata(source.id)
            for page in metadata["evidence"]:
                markdown = f"# {page['title']}\n\nSource: {page['url']}\n\n{page['attribution']}\n\n{page.pop('markdown')}\n"
                if (
                    page["id"] in evidence_text
                    and evidence_text[page["id"]] != markdown
                ):
                    raise ValueError(
                        "Entities must retain the same full content for shared evidence"
                    )
                evidence_text[page["id"]] = markdown
                (markdown_dir / f"{page['id']}.md").write_text(
                    markdown, encoding="utf-8", newline="\n"
                )
            catalog.write(json.dumps(metadata, ensure_ascii=False) + "\n")
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source.id,
        "adapter_version": source.version,
        "snapshot": snapshot_id,
        "archive": archive.path.name,
        "entities": len(entities),
        "evidence_pages": len(evidence_text),
        "crawl": archive.counts(),
        "excluded_from_entities": excluded,
    }
    atomic_json(snapshot / "manifest.json", manifest)
    atomic_json(current, manifest)
    return snapshot


def build(source: Source, root: Path, *, archive_id: str | None = None) -> Path:
    source_dir = root / source.id
    source_dir.mkdir(parents=True, exist_ok=True)
    with FileLock(source_dir / "writer.lock", timeout=0):
        active = source_dir / "work.json"
        if archive_id is None:
            if active.exists():
                work = json.loads(active.read_text(encoding="utf-8"))
                if work["adapter_version"] != source.version:
                    raise RuntimeError(
                        "Incomplete crawl uses a different adapter version; inspect work.json before retrying"
                    )
            else:
                work = {"archive": run_id(), "adapter_version": source.version}
                atomic_json(active, work)
            archive_id = work["archive"]
            online = True
        else:
            online = False
        if not re.fullmatch(r"[A-Za-z0-9_-]+", archive_id):
            raise ValueError("Invalid archive ID")
        archive_path = source_dir / "archives" / archive_id
        if not online and not (archive_path / "manifest.sqlite").is_file():
            raise FileNotFoundError(f"Archive not found: {archive_path}")
        archive = Archive(archive_path)
        try:
            if online:
                crawl(source, archive)
            snapshot = publish(source, archive, source_dir)
            if online:
                active.unlink()
            return snapshot
        finally:
            archive.close()
