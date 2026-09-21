import json
import subprocess
from collections import Counter
from contextlib import closing
from pathlib import Path

from pipelines.archive import Archive
from pipelines.distribution import collect_artifacts
from pipelines.sources.base import AuditedSource, PreparedSource, Source


def audit(source: Source, root: Path, binary: Path | None = None) -> dict:
    """Verify a published snapshot and optionally compare its offline CLI exports."""
    source_dir = root / source.id
    manifest = json.loads((source_dir / "published/current.json").read_text())
    entities, artifacts, _ = collect_artifacts(root, [source.id])
    html = {key.split("/", 1)[1]: body for key, body in artifacts.items()}
    if len(entities) < source.minimum_entities:
        raise ValueError(f"Too few entities for {source.id}: {len(entities)}")
    if len(html) != manifest["evidence_pages"]:
        raise ValueError("Evidence page count mismatch")
    archive_path = source_dir / "archives" / manifest["archive"]
    completion = archive_path / "complete.json"
    if (
        not completion.is_file()
        or json.loads(completion.read_text())["source"] != source.id
    ):
        raise ValueError("Archive has no matching completion record")
    with closing(Archive(archive_path)) as archive:
        if archive.pages("pending", "failed"):
            raise ValueError("Archive is incomplete")
        pages = {page["url"]: page for page in archive.pages("saved")}
        captured_bytes = sum(len(archive.body(page)) for page in pages.values())
        if isinstance(source, PreparedSource):
            source.prepare((url, archive.body(page)) for url, page in pages.items())
        checks = (
            source.audit(
                entities,
                {url: archive.body(page) for url, page in pages.items()},
                html,
            )
            if isinstance(source, AuditedSource)
            else {}
        )
        export_pages = 0
        if binary:
            for entity in entities:
                for page in entity["evidence"]:
                    for format, expected in (
                        ("source", archive.body(pages[page["url"]])),
                        ("markdown", page["markdown"].encode()),
                        ("html", html[page["id"]]),
                    ):
                        exported = subprocess.run(
                            [
                                str(binary.resolve()),
                                "get",
                                "--format",
                                format,
                                "--evidence",
                                page["id"],
                                entity["id"],
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        ).stdout
                        if exported != expected:
                            raise ValueError(
                                f"CLI export mismatch: {entity['id']} {format}"
                            )
                    export_pages += 1
    return {
        "source": source.id,
        "archive": manifest["archive"],
        "snapshot": manifest["snapshot"],
        "captured_responses": len(pages),
        "captured_bytes": captured_bytes,
        "entities": len(entities),
        "evidence_pages": len(html),
        "kinds": dict(Counter(entity["kind"] for entity in entities)),
        "source_checks": checks,
        "cli_export_pages_checked": export_pages,
        "ok": True,
    }
