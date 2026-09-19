import json
import re
import sqlite3
from pathlib import Path
from types import TracebackType
from typing import Self
from urllib.parse import urldefrag

from pipelines.model import SCHEMA_VERSION


def status(source_dir: Path) -> dict:
    result: dict = {"published": None, "work": None}
    published = source_dir / "published"
    if (published / "current.json").is_file():
        with Dataset(published) as dataset:
            result["published"] = {**dataset.manifest, "facets": dataset.facets()}
    try:
        work = json.loads((source_dir / "work.json").read_text(encoding="utf-8"))
    except FileNotFoundError:
        return result
    archive_id = work["archive"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", archive_id):
        raise ValueError("Invalid archive ID")
    result["work"] = work
    try:
        result["work"]["progress"] = json.loads(
            (source_dir / "archives" / archive_id / "progress.json").read_text(
                encoding="utf-8"
            )
        )
    except FileNotFoundError:
        pass
    return result


class Dataset:
    def __init__(self, published: Path) -> None:
        # Pin a snapshot for this reader's lifetime, even if another run publishes.
        self.manifest = json.loads(
            (published / "current.json").read_text(encoding="utf-8")
        )
        if self.manifest["schema_version"] != SCHEMA_VERSION:
            raise ValueError("Unsupported reference-data schema")
        snapshot_id = self.manifest["snapshot"]
        if not re.fullmatch(r"[A-Za-z0-9_-]+", snapshot_id):
            raise ValueError("Invalid snapshot ID")
        self.snapshot = published / "snapshots" / snapshot_id
        uri = (
            self.snapshot / "index.sqlite"
        ).resolve().as_uri() + "?mode=ro&immutable=1"
        self.db = sqlite3.connect(uri, uri=True)
        self.db.row_factory = sqlite3.Row
        # Keep a bounded working set across queries, including Docker bind mounts.
        self.db.execute("PRAGMA cache_size=-65536")

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        category: str | None = None,
        limit: int = 20,
        offset: int = 0,
        prefix: bool = False,
    ) -> list[dict]:
        if not 1 <= limit <= 100 or not 0 <= offset <= 10000:
            raise ValueError("Limit must be 1-100 and offset 0-10000")
        if len(query) > 500:
            raise ValueError("Query is limited to 500 characters")
        terms = re.findall(r"\w+", query, re.UNICODE)
        filters = "(? IS NULL OR d.kind=?) AND (? IS NULL OR EXISTS (SELECT 1 FROM categories c WHERE c.document_id=d.id AND c.category=?))"
        parameters = (kind, kind, category, category)
        snippets: dict[int, str] = {}
        if not terms:
            if query.strip():
                return []
            rows = self.db.execute(
                f"SELECT d.id, d.title, d.url, d.kind, NULL AS section_id FROM documents d WHERE {filters} ORDER BY d.title, d.id LIMIT ? OFFSET ?",
                (*parameters, limit, offset),
            ).fetchall()
        else:
            expression = " AND ".join(
                '"' + term.replace('"', '""') + '"' for term in terms
            )
            if prefix:
                expression += "*"
            key = "".join(
                character for character in query.casefold() if character.isalnum()
            )
            rows = self.db.execute(
                f"""
                WITH matches AS MATERIALIZED (
                    SELECT rowid AS section_id, document_id, bm25(sections, 0, 10, 8, 1) AS score
                    FROM sections WHERE sections MATCH ?
                ), ranked AS (
                    SELECT *, row_number() OVER (PARTITION BY document_id ORDER BY score) AS position FROM matches
                ), best AS MATERIALIZED (
                    SELECT document_id, section_id, score FROM ranked WHERE position=1
                ), exact AS MATERIALIZED (
                    SELECT DISTINCT document_id FROM names WHERE name=?
                ), candidates AS (
                    SELECT document_id, section_id, score FROM best
                    UNION ALL
                    SELECT e.document_id, NULL, 0 FROM exact e
                    WHERE NOT EXISTS (SELECT 1 FROM best b WHERE b.document_id=e.document_id)
                )
                SELECT d.id, d.title, d.url, d.kind, c.section_id
                FROM candidates c JOIN documents d ON d.id=c.document_id
                WHERE {filters}
                ORDER BY EXISTS (SELECT 1 FROM exact e WHERE e.document_id=d.id) DESC,
                    c.score, d.title, d.id LIMIT ? OFFSET ?
            """,
                (expression, key, *parameters, limit, offset),
            ).fetchall()
            section_ids = [
                row["section_id"] for row in rows if row["section_id"] is not None
            ]
            if section_ids:
                placeholders = ",".join("?" for _ in section_ids)
                snippets = dict(
                    self.db.execute(
                        f"SELECT rowid, snippet(sections, 3, '[', ']', '...', 30) FROM sections WHERE sections MATCH ? AND rowid IN ({placeholders})",
                        (expression, *section_ids),
                    ).fetchall()
                )
        return [
            {
                "source": self.manifest["source"],
                "id": row["id"],
                "title": row["title"],
                "url": row["url"],
                "kind": row["kind"],
                "snippet": snippets.get(row["section_id"], ""),
            }
            for row in rows
        ]

    def read(self, document_id: str) -> dict:
        row = self.db.execute(
            "SELECT metadata FROM documents WHERE id=?", (document_id,)
        ).fetchone()
        if row is None:
            raise KeyError(document_id)
        metadata: dict = json.loads(row["metadata"])
        metadata["markdown"] = (
            self.snapshot / "markdown" / f"{document_id}.md"
        ).read_text(encoding="utf-8")
        return metadata

    def facets(self) -> dict[str, list[dict]]:
        return {
            "kinds": [
                dict(row)
                for row in self.db.execute(
                    "SELECT kind AS value, count(*) AS count FROM documents GROUP BY kind"
                )
            ],
            "categories": [
                dict(row)
                for row in self.db.execute(
                    "SELECT category AS value, count(DISTINCT document_id) AS count FROM categories GROUP BY category"
                )
            ],
        }

    def read_url(self, url: str) -> dict:
        row = self.db.execute(
            "SELECT id FROM documents WHERE url=?", (urldefrag(url).url,)
        ).fetchone()
        if row is None:
            raise KeyError(url)
        return self.read(row["id"])


def search_all(datasets: list[Dataset], query: str, *, limit: int = 20) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("Limit must be 1-100")
    results = []
    for dataset in datasets:
        for rank, hit in enumerate(dataset.search(query, limit=limit), start=1):
            results.append({**hit, "score": 1 / (60 + rank)})
    return sorted(results, key=lambda hit: (-hit["score"], hit["source"], hit["id"]))[
        :limit
    ]
