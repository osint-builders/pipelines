import hashlib
import json
import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ-") + uuid4().hex[:8]


class Archive:
    def __init__(self, path: Path) -> None:
        self.path = path
        (path / "html").mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path / "manifest.sqlite")
        self.db.row_factory = sqlite3.Row
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS pages (
                url TEXT PRIMARY KEY, state TEXT NOT NULL DEFAULT 'pending',
                status INTEGER, content_type TEXT, file TEXT, sha256 TEXT,
                fetched_at TEXT, headers TEXT, error TEXT
            )
        """)
        self.db.commit()

    def add(self, url: str) -> bool:
        cursor = self.db.execute("INSERT OR IGNORE INTO pages(url) VALUES (?)", (url,))
        self.db.commit()
        return cursor.rowcount > 0

    def save(
        self, url: str, status: int, body: bytes, content_type: str, headers: dict
    ) -> None:
        digest = hashlib.sha256(body).hexdigest()
        file = f"html/{hashlib.sha256(url.encode()).hexdigest()}.html"
        target = self.path / file
        temporary = target.with_suffix(".tmp")
        with temporary.open("wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        state = (
            "saved"
            if status == 200
            else "unavailable"
            if status in {404, 410}
            else "failed"
        )
        self.db.execute(
            """
            UPDATE pages SET state=?, status=?, content_type=?, file=?, sha256=?,
                fetched_at=?, headers=?, error=NULL WHERE url=?
        """,
            (
                state,
                status,
                content_type,
                file,
                digest,
                datetime.now(UTC).isoformat(),
                json.dumps(headers),
                url,
            ),
        )
        self.db.commit()

    def fail(self, url: str, error: str, *, excluded: bool = False) -> None:
        self.db.execute(
            "UPDATE pages SET state=?, error=? WHERE url=?",
            ("excluded" if excluded else "failed", error, url),
        )
        self.db.commit()

    def pages(self, *states: str) -> list[sqlite3.Row]:
        placeholders = ",".join("?" for _ in states)
        return self.db.execute(
            f"SELECT * FROM pages WHERE state IN ({placeholders}) ORDER BY url", states
        ).fetchall()

    def body(self, page: sqlite3.Row) -> bytes:
        body = (self.path / str(page["file"])).read_bytes()
        if hashlib.sha256(body).hexdigest() != page["sha256"]:
            raise ValueError(f"Archive checksum mismatch: {page['url']}")
        return body

    def counts(self) -> dict[str, int]:
        return dict(
            self.db.execute(
                "SELECT state, count(*) FROM pages GROUP BY state"
            ).fetchall()
        )

    def mark_complete(self, source: str) -> None:
        atomic_json(
            self.path / "complete.json", {"source": source, "counts": self.counts()}
        )
        self.checkpoint()

    def checkpoint(self) -> None:
        atomic_json(
            self.path / "progress.json",
            {
                "archive": self.path.name,
                "counts": self.counts(),
                "updated_at": datetime.now(UTC).isoformat(),
            },
        )

    def close(self) -> None:
        self.db.close()
