import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from filelock import FileLock

from pipelines.archive import Archive, atomic_json, run_id
from pipelines.crawl import crawl
from pipelines.model import SCHEMA_VERSION, Document
from pipelines.sources.base import Source


def name_key(value: str) -> str:
    return "".join(character for character in value.casefold() if character.isalnum())


def create_index(path: Path, documents: list[Document]) -> None:
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript("""
            CREATE TABLE documents(id TEXT PRIMARY KEY, url TEXT UNIQUE, title TEXT,
                kind TEXT, language TEXT, metadata TEXT);
            CREATE INDEX document_kind ON documents(kind);
            CREATE TABLE names(name TEXT, document_id TEXT);
            CREATE INDEX exact_names ON names(name);
            CREATE TABLE categories(category TEXT, document_id TEXT);
            CREATE INDEX category_filter ON categories(category, document_id);
            CREATE TABLE facts(document_id TEXT, name TEXT, raw TEXT, metadata TEXT);
            CREATE INDEX fact_names ON facts(name, document_id);
            CREATE VIRTUAL TABLE sections USING fts5(document_id UNINDEXED,
                title, names, body, tokenize='unicode61 remove_diacritics 2', prefix='2 3');
        """)
        for document in documents:
            metadata = document.metadata()
            metadata.pop("markdown")
            db.execute(
                "INSERT INTO documents VALUES(?,?,?,?,?,?)",
                (
                    document.id,
                    document.url,
                    document.title,
                    document.kind,
                    document.language,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            db.executemany(
                "INSERT INTO names VALUES(?,?)",
                ((name_key(name), document.id) for name in set(document.names)),
            )
            db.executemany(
                "INSERT INTO categories VALUES(?,?)",
                ((category, document.id) for category in document.categories),
            )
            db.executemany(
                "INSERT INTO facts VALUES(?,?,?,?)",
                (
                    (document.id, fact["name"], fact["raw"], json.dumps(fact))
                    for fact in metadata["facts"]
                ),
            )
            sections = re.split(r"\n(?=#{1,6} )", document.markdown)
            for section in sections:
                db.execute(
                    "INSERT INTO sections VALUES(?,?,?,?)",
                    (document.id, document.title, " ".join(document.names), section),
                )
        db.execute("INSERT INTO sections(sections) VALUES ('optimize')")
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("Index integrity check failed")
        db.execute("INSERT INTO sections(sections) VALUES ('integrity-check')")


def publish(source: Source, archive: Archive, source_dir: Path) -> Path:
    if archive.pages("pending", "failed"):
        raise RuntimeError("An incomplete archive cannot be published")
    completion = archive.path / "complete.json"
    if (
        not completion.is_file()
        or json.loads(completion.read_text(encoding="utf-8"))["source"] != source.id
    ):
        raise RuntimeError(
            "Archive has no completion record; finish the explicit scrape first"
        )
    labels: dict[str, list[str]] = {}
    pages = archive.pages("saved")
    for page in pages:
        for url, names in source.labels(page["url"], archive.body(page)).items():
            labels.setdefault(url, []).extend(names)
    documents: list[Document] = []
    excluded: list[str] = []
    for page in pages:
        headers = json.loads(page["headers"])
        if any(
            key.lower() == "x-robots-tag" and "noindex" in value.lower()
            for key, value in headers.items()
        ):
            excluded.append(page["url"])
            continue
        document = source.extract(
            page["url"], archive.body(page), labels.get(page["url"], [])
        )
        if document is None:
            excluded.append(page["url"])
        else:
            document.retrieved_at = page["fetched_at"]
            document.html_sha256 = page["sha256"]
            documents.append(document)
    if len(documents) < source.minimum_documents:
        raise RuntimeError(
            f"Only {len(documents)} documents; expected at least {source.minimum_documents}"
        )
    published = source_dir / "published"
    published.mkdir(exist_ok=True)
    current = published / "current.json"
    if current.exists():
        previous = json.loads(current.read_text(encoding="utf-8"))
        if len(documents) < previous["documents"] * 0.9:
            raise RuntimeError("Corpus shrank by more than 10%; refusing replacement")
    snapshot_id = run_id()
    snapshot = published / "snapshots" / snapshot_id
    snapshot.mkdir(parents=True)
    markdown_dir = snapshot / "markdown"
    markdown_dir.mkdir()
    with (snapshot / "documents.jsonl").open("w", encoding="utf-8") as catalog:
        for document in documents:
            (markdown_dir / f"{document.id}.md").write_text(
                f"# {document.title}\n\nSource: {document.url}\n\n{document.attribution}\n\n{document.markdown}\n",
                encoding="utf-8",
            )
            metadata = document.metadata()
            metadata.pop("markdown")
            catalog.write(json.dumps(metadata, ensure_ascii=False) + "\n")
    create_index(snapshot / "index.sqlite", documents)
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "source": source.id,
        "adapter_version": source.version,
        "snapshot": snapshot_id,
        "archive": archive.path.name,
        "documents": len(documents),
        "crawl": archive.counts(),
        "excluded_from_index": excluded,
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
