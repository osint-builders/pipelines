import hashlib
import json
import socket
import subprocess
import sys
from contextlib import closing
from pathlib import Path

import pytest
from test_cambridgepixel import SEED, CambridgePixel
from test_cambridgepixel import page as collection_page
from test_militaryperiscope import MilitaryPeriscope, captures
from test_pipeline import HTML, URL, SmallSource

from pipelines.__main__ import main
from pipelines.archive import Archive, atomic_json
from pipelines.audit import audit
from pipelines.build import publish
from pipelines.snapshot import load_snapshot
from pipelines.sources.base import Source


@pytest.fixture(params=["html", "api", "collection"])
def published(
    request: pytest.FixtureRequest, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Source, Path]:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Audit attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    source: Source
    if request.param == "api":
        source, responses = MilitaryPeriscope(), captures()
    elif request.param == "collection":
        monkeypatch.setattr(
            "pipelines.sources.cambridgepixel.imagery.reviews", lambda: []
        )
        source, responses = CambridgePixel(), {SEED: collection_page()}
    else:
        source, responses = SmallSource(), {URL: HTML}
    source.minimum_entities = 1
    source_dir = tmp_path / source.id
    with closing(Archive(source_dir / "archives" / "fixture")) as archive:
        for url, body in responses.items():
            archive.add(url)
            archive.save(
                url,
                200,
                body,
                "application/json" if body.startswith(b"{") else "text/html",
                {},
            )
        archive.mark_complete(source.id)
        snapshot = publish(source, archive, source_dir)
    return source, snapshot


def test_shared_audit_checks_html_api_and_collection_snapshots_offline(
    published: tuple[Source, Path], tmp_path: Path
) -> None:
    source, snapshot = published
    result = audit(source, tmp_path)
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert result["source"] == source.id
    assert result["ok"] is True
    assert result["entities"] == manifest["entities"]
    assert result["evidence_pages"] == manifest["evidence_pages"]
    assert result["captured_responses"] == manifest["crawl"]["saved"]
    assert result["captured_bytes"] > 0
    assert result["cli_export_pages_checked"] == 0
    if source.id == "militaryperiscope":
        assert result["source_checks"]["complete_accessible_trial"] is True
        assert result["source_checks"]["content_fragments_checked"] > 0
    elif source.id == "cambridgepixel":
        assert result["source_checks"]["catalog_rows"] == 2
        assert result["source_checks"]["checked_facts"] == 14
    else:
        assert result["source_checks"] == {}


def test_shared_command_writes_the_same_report_as_stdout(
    published: tuple[Source, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, _ = published
    report = tmp_path / "reports" / "audit.json"
    monkeypatch.setattr("pipelines.registry.get_source", lambda name: source)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "pipeline-build",
            "audit",
            source.id,
            "--root",
            str(tmp_path),
            "--output",
            str(report),
        ],
    )
    main()
    result = json.loads(capsys.readouterr().out)
    assert result == json.loads(report.read_text())
    assert result["source"] == source.id and result["ok"] is True


def test_audit_rejects_changed_archived_content(
    published: tuple[Source, Path], tmp_path: Path
) -> None:
    source, _ = published
    with closing(Archive(tmp_path / source.id / "archives" / "fixture")) as archive:
        record = archive.pages("saved")[0]
        (archive.path / record["file"]).write_bytes(b"Corrupted response")
    with pytest.raises(ValueError, match="checksum"):
        audit(source, tmp_path)


def test_audit_rejects_incomplete_archives(
    published: tuple[Source, Path], tmp_path: Path
) -> None:
    source, _ = published
    with closing(Archive(tmp_path / source.id / "archives" / "fixture")) as archive:
        archive.add("https://example.test/missing")
    with pytest.raises(ValueError, match="incomplete"):
        audit(source, tmp_path)


def test_audit_compares_each_cli_export_to_its_capture(
    published: tuple[Source, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, snapshot = published
    _, entities = load_snapshot(tmp_path / source.id)
    exports = {}
    with closing(Archive(tmp_path / source.id / "archives" / "fixture")) as archive:
        responses = {page["url"]: archive.body(page) for page in archive.pages("saved")}
    for entity in entities:
        for evidence in entity["evidence"]:
            rendered = snapshot / "html" / (evidence["id"] + ".html")
            for format, expected in (
                ("source", responses[evidence["url"]]),
                ("markdown", evidence["markdown"].encode()),
                (
                    "html",
                    rendered.read_bytes()
                    if rendered.exists()
                    else responses[evidence["url"]],
                ),
            ):
                exports[(format, evidence["id"], entity["id"])] = expected
    calls: list[tuple[str, str, str]] = []
    corrupt = False

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert command[:3] == [
            str((tmp_path / "pipelines").resolve()),
            "get",
            "--format",
        ]
        assert command[4] == "--evidence"
        key = command[3], command[5], command[6]
        calls.append(key)
        return subprocess.CompletedProcess(
            command, 0, stdout=b"Wrong export" if corrupt else exports[key]
        )

    monkeypatch.setattr("pipelines.audit.subprocess.run", run)
    result = audit(source, tmp_path, tmp_path / "pipelines")
    assert set(calls) == set(exports)
    assert result["cli_export_pages_checked"] == len(exports) // 3
    corrupt = True
    with pytest.raises(ValueError, match="CLI export mismatch"):
        audit(source, tmp_path, tmp_path / "pipelines")


def test_missing_snapshot_does_not_create_an_archive(tmp_path: Path) -> None:
    root = tmp_path / "missing"
    with pytest.raises(FileNotFoundError):
        audit(SmallSource(), root)
    assert not root.exists()


def test_source_checks_detect_missing_text_even_with_matching_html_checksum(
    tmp_path: Path,
) -> None:
    source = MilitaryPeriscope()
    source.minimum_entities = 1
    source_dir = tmp_path / source.id
    with closing(Archive(source_dir / "archives" / "fixture")) as archive:
        for url, body in captures().items():
            archive.add(url)
            archive.save(url, 200, body, "application/json", {})
        archive.mark_complete(source.id)
        snapshot = publish(source, archive, source_dir)
    catalog = snapshot / "entities.jsonl"
    entities = [json.loads(line) for line in catalog.read_text().splitlines()]
    evidence = entities[0]["evidence"][0]
    rendered = snapshot / "html" / (evidence["id"] + ".html")
    body = rendered.read_bytes().replace(b"unmodified source wording", b"REMOVED")
    rendered.write_bytes(body)
    evidence["html_sha256"] = hashlib.sha256(body).hexdigest()
    catalog.write_text("".join(json.dumps(entity) + "\n" for entity in entities))
    with pytest.raises(ValueError, match="Missing content"):
        audit(source, tmp_path)


def test_source_checks_detect_missing_catalog_subject(tmp_path: Path) -> None:
    source = MilitaryPeriscope()
    source.minimum_entities = 1
    source_dir = tmp_path / source.id
    with closing(Archive(source_dir / "archives" / "fixture")) as archive:
        for url, body in captures().items():
            archive.add(url)
            archive.save(url, 200, body, "application/json", {})
        archive.mark_complete(source.id)
        snapshot = publish(source, archive, source_dir)
    catalog = snapshot / "entities.jsonl"
    entities = [json.loads(line) for line in catalog.read_text().splitlines()]
    removed = entities.pop()
    catalog.write_text("".join(json.dumps(entity) + "\n" for entity in entities))
    for path in (snapshot / "manifest.json", source_dir / "published/current.json"):
        manifest = json.loads(path.read_text())
        manifest["entities"] -= 1
        manifest["evidence_pages"] -= len(removed["evidence"])
        atomic_json(path, manifest)
    with pytest.raises(ValueError, match="Trial subjects missing"):
        audit(source, tmp_path)
