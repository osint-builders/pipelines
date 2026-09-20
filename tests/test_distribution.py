import json
import zipfile
from pathlib import Path

import pytest
from test_pipeline import HTML, archived

from pipelines.build import publish
from pipelines.distribution import collect_artifacts, content_digest, write_bundle


def test_export_preserves_full_content_and_stable_source_id(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    try:
        snapshot = publish(source, archive, source_dir)
    finally:
        archive.close()
    entities, bodies, _ = collect_artifacts(tmp_path, [source.id])
    document = entities[0]
    assert document["id"] == f"{source.id}:{document['source_id']}"
    assert bodies[document["source"] + "/" + document["evidence"][0]["id"]] == HTML
    assert document["evidence"][0]["markdown"] == (
        snapshot / "markdown" / f"{document['evidence'][0]['id']}.md"
    ).read_text(encoding="utf-8")
    assert document["facts"][0]["evidence"].startswith(document["url"])


def test_corrupt_archive_cannot_be_distributed(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    publish(source, archive, source_dir)
    page = archive.pages("saved")[0]
    (archive.path / page["file"]).write_bytes(b"changed after publication")
    archive.close()
    with pytest.raises(ValueError, match="checksum"):
        collect_artifacts(tmp_path, [source.id])


def test_digest_ignores_capture_metadata_but_detects_content_edits_and_deletions() -> (
    None
):
    original = [
        {
            "id": "one",
            "title": "Radar",
            "html_sha256": "abc",
            "retrieved_at": "yesterday",
        },
        {"id": "two"},
    ]
    later = [{**original[0], "retrieved_at": "today"}, original[1]]
    assert content_digest(original) == content_digest(later)
    assert content_digest(original) != content_digest(
        [{**original[0], "title": "New radar"}, original[1]]
    )
    assert content_digest(original) != content_digest(original[:1])
    assert content_digest(original) == content_digest(
        [{**original[0], "html_sha256": "def"}, original[1]]
    )


def test_bundle_is_deterministic_and_preserves_binary_payload(tmp_path: Path) -> None:
    members = {
        "html/page.html": b"\xff\x00\r\n",
        "manifest.json": json.dumps({"entities": 1}).encode(),
    }
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    write_bundle(first, members)
    write_bundle(second, dict(reversed(list(members.items()))))
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.read("html/page.html") == members["html/page.html"]
