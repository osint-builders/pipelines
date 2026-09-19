import importlib
import json
import socket
import sqlite3
from pathlib import Path

import pytest
from filelock import FileLock, Timeout

from pipelines.archive import Archive
from pipelines.build import build, publish
from pipelines.reader import Dataset, search_all, status
from pipelines.sources.radartutorial import ORIGIN, Radartutorial, normalize_fact

URL = f"{ORIGIN}/19.kartei/03.atc/en/karte005.en.html"
HTML = b"""<!doctype html><html lang="en"><head><title>ASR 12 - Radar Basics</title></head>
<body><nav>Unrelated navigation</nav><div class="content">
<h4 class="hh_no">ASR 12</h4><h4 class="hh_yes">ASR 12</h4>
<table class="ttd"><tr><td>Frequency:</td><td>2.7-2.9 GHz</td></tr>
<tr><td>Pulse width:</td><td><span class="explan" title="Short-range mode">1 us</span>
and <span class="explan" title="pulsecompressed long-range mode">55 us</span></td></tr>
<tr><td>PRF:</td><td></td></tr></table>
<p>A solid state airport surveillance radar with pulse compression.</p>
<div class="prn">Duplicate print material</div><img src="../../diagram.png" alt="">
<a class="lupe-r" href="picture.jpg" title="Image copyright ACME 2026"></a>
<p><a href="manual.pdf">Manufacturer's leaflet</a></p>
</div><ul class="source"><li>Individual image copyright</li></ul></body></html>"""


class SmallSource(Radartutorial):
    minimum_documents = 1
    seeds: tuple[str, ...] = (URL,)


def archived(tmp_path: Path) -> tuple[SmallSource, Archive, Path]:
    source = SmallSource()
    source_dir = tmp_path / source.id
    archive = Archive(source_dir / "archives" / "fixture")
    archive.add(URL)
    archive.save(URL, 200, HTML, "text/html", {})
    archive.mark_complete(source.id)
    return source, archive, source_dir


def test_extract_keeps_evidence_and_removes_layout_duplicates() -> None:
    document = SmallSource().extract(URL, HTML, ["ASR-12"])
    assert document is not None
    assert document.title == "ASR 12"
    assert document.names == ["ASR 12", "ASR-12"]
    assert document.facts[0].values == [2.7, 2.9]
    assert document.facts[0].evidence == URL
    assert "Short-range mode" in document.facts[1].raw
    assert "long-range mode" in document.markdown
    assert len(document.facts) == 2
    assert "Unrelated navigation" not in document.markdown
    assert "Duplicate print" not in document.markdown
    assert document.markdown.count("ASR 12") == 1
    assert "Individual image copyright" in document.markdown
    assert "Image copyright ACME 2026" in document.markdown
    assert "https://www.radartutorial.eu/19.kartei/diagram.png" in document.markdown
    assert document.links == [
        f"{ORIGIN}/19.kartei/03.atc/en/manual.pdf",
        f"{ORIGIN}/19.kartei/03.atc/en/picture.jpg",
    ]


@pytest.mark.parametrize(
    ("raw", "values", "unit"),
    [
        ("2,700-3,000 MHz", [2700, 3000], "MHz"),
        ("24,0...24,25 GHz", [24, 24.25], "GHz"),
        ("0,96°", [0.96], "°"),
        ("1\u202f030, 1\u202f090 MHz", [], None),
        ("60/80/100 NM", [], None),
        ("up to 220 NM = 410 km", [], None),
    ],
)
def test_numeric_normalization_is_conservative(
    raw: str, values: list[float], unit: str | None
) -> None:
    fact = normalize_fact("frequency", raw, URL)
    assert (fact.values, fact.unit, fact.raw) == (values, unit, raw)


def test_scope_discovery_and_noindex() -> None:
    source = SmallSource()
    assert source.normalize(URL + "?utm_test=1#section") == URL
    assert (
        source.normalize(f"{ORIGIN}/01.basics/en/Duty cycle.en.html")
        == f"{ORIGIN}/01.basics/en/Duty%20cycle.en.html"
    )
    for url in [
        "https://other.example/index.en.html",
        f"{ORIGIN}/index.de.html",
        f"{ORIGIN}/bin/test.en.html",
        f"{ORIGIN}/%2e%2e/test.en.html",
    ]:
        assert source.normalize(url) is None
    body = f"<urlset><url><loc>{URL}</loc></url><url><loc>{ORIGIN}/index.de.html</loc></url></urlset>".encode()
    assert source.discover(source.seeds[0] + ".xml", body) == [URL]
    assert (
        source.extract(
            URL,
            HTML.replace(b"<head>", b'<head><meta name="robots" content="noindex">'),
            [],
        )
        is None
    )


def test_index_names_do_not_turn_related_prose_into_aliases() -> None:
    source = SmallSource()
    body = f'<a href="{URL}" title="ASR-12">1</a>'.encode()
    assert source.labels(f"{ORIGIN}/logos/hersteller.html", body) == {URL: ["ASR-12"]}
    assert source.labels(f"{ORIGIN}/19.kartei/en/ka03.en.html", body) == {
        URL: ["ASR-12"]
    }
    assert source.labels(f"{ORIGIN}/19.kartei/03.atc/en/karte001.en.html", body) == {}


def test_script_navigation_links_are_discovered_without_execution() -> None:
    source = SmallSource()
    url = f"{ORIGIN}/19.kartei/en/ka02.en.html"
    body = b'<script>var page=new Array("../03.atc/en/karte005.en.html");</script>'
    assert source.discover(url, body) == [URL]
    assert source.extract(url, body, []) is None


def test_malformed_language_comment_does_not_hide_the_article() -> None:
    broken = HTML.replace(
        b"<nav>Unrelated navigation", b"<nav><!-- Unclosed language menu"
    )
    document = SmallSource().extract(URL, broken, [])
    assert document is not None
    assert document.title == "ASR 12"
    assert document.facts[0].raw == "2.7-2.9 GHz"


def test_unclosed_mobile_heading_does_not_delete_the_article() -> None:
    broken = HTML.replace(
        b'<h4 class="hh_yes">ASR 12</h4>', b'<h5 class="hh_yes">ASR 12'
    )
    document = SmallSource().extract(URL, broken, [])
    assert document is not None
    assert "airport surveillance radar" in document.markdown
    assert len(document.facts) == 2


def test_image_only_help_page_is_preserved() -> None:
    body = b'<html><title>Help - Radartutorial</title><body><a href="javascript:close()"><img src="help.png"></a></body></html>'
    document = SmallSource().extract(f"{ORIGIN}/html/help02.en.html", body, [])
    assert document is not None
    assert document.title == "Help"
    assert f"{ORIGIN}/html/help.png" in document.markdown
    assert "javascript" not in document.markdown


def test_scientific_subscripts_and_exponents_are_preserved() -> None:
    body = HTML.replace(b"2.7-2.9 GHz", b"10<sup>3</sup> Hz").replace(
        b"Frequency:", b"f<sub>0</sub>:"
    )
    document = SmallSource().extract(URL, body, [])
    assert document is not None
    assert "<sup>3</sup>" in document.markdown
    assert "<sub>0</sub>" in document.markdown
    assert document.facts[0].name == "f _(0)"
    assert document.facts[0].raw == "10 ^(3) Hz"
    assert document.facts[0].values == []


def test_archive_offline_rebuild_search_and_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, archive, source_dir = archived(tmp_path)
    assert archive.body(archive.pages("saved")[0]) == HTML
    archive.close()

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline rebuild attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    snapshot = build(source, tmp_path, archive_id="fixture")
    with Dataset(source_dir / "published") as dataset:
        assert dataset.search("ASR-12")[0]["title"] == "ASR 12"
        assert dataset.search("pulse compression")[0]["url"] == URL
        assert dataset.search("ASR 12", kind="article") == []
        assert dataset.search("", category="03.atc")[0]["url"] == URL
        assert dataset.search("-") == []
        assert dataset.search('" nonexistent OR *') == []
        hit = dataset.search("airport")[0]
        document = dataset.read(hit["id"])
        assert dataset.read_url(URL + "#technical-data")["id"] == hit["id"]
        assert "Short-range mode" in document["markdown"]
        assert document["facts"][0]["raw"] == "2.7-2.9 GHz"
        assert dataset.facets()["kinds"] == [{"value": "radar", "count": 1}]
        assert search_all([dataset], "ASR 12")[0]["id"] == hit["id"]
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            dataset.db.execute("DELETE FROM documents")
    assert (snapshot / "documents.jsonl").is_file()


def test_failed_build_keeps_current_and_reader_pins_snapshot(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    publish(source, archive, source_dir)
    current = source_dir / "published" / "current.json"
    before = current.read_bytes()
    with Dataset(source_dir / "published") as reader:
        source.minimum_documents = 2
        with pytest.raises(RuntimeError, match="at least"):
            publish(source, archive, source_dir)
        assert current.read_bytes() == before
        source.minimum_documents = 1
        archive.save(URL, 200, HTML.replace(b"ASR 12", b"ASR 13"), "text/html", {})
        publish(source, archive, source_dir)
        assert reader.search("ASR 12")[0]["title"] == "ASR 12"
        with Dataset(source_dir / "published") as newer:
            assert newer.search("ASR 13")[0]["title"] == "ASR 13"
    archive.close()


def test_corruption_pending_work_and_writer_lock_are_rejected(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    page = archive.pages("saved")[0]
    (archive.path / page["file"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        publish(source, archive, source_dir)
    archive.add(f"{ORIGIN}/index.en.html")
    with pytest.raises(RuntimeError, match="incomplete"):
        publish(source, archive, source_dir)
    archive.close()
    with FileLock(source_dir / "writer.lock"):
        with pytest.raises(Timeout):
            build(source, tmp_path, archive_id="fixture")
    assert not (source_dir / "published" / "current.json").exists()


def test_missing_snapshot_read_does_not_create_data(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        Dataset(tmp_path / "missing")
    assert list(tmp_path.iterdir()) == []
    assert status(tmp_path / "missing") == {"published": None, "work": None}
    assert list(tmp_path.iterdir()) == []


def test_missing_archive_reindex_does_not_fetch(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        build(SmallSource(), tmp_path, archive_id="missing")
    assert not (tmp_path / "radartutorial" / "archives").exists()


def test_broken_pointer_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "current.json").write_text(
        json.dumps({"schema_version": 1, "snapshot": "../../private"})
    )
    with pytest.raises(ValueError, match="snapshot"):
        Dataset(tmp_path)


def test_complete_marker_is_required_even_if_no_requests_are_pending(
    tmp_path: Path,
) -> None:
    source, archive, source_dir = archived(tmp_path)
    (archive.path / "complete.json").unlink()
    with pytest.raises(RuntimeError, match="completion record"):
        publish(source, archive, source_dir)
    archive.close()


def test_index_failure_cannot_replace_published_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, archive, source_dir = archived(tmp_path)
    publish(source, archive, source_dir)
    current = source_dir / "published" / "current.json"
    before = current.read_bytes()

    def fail_index(path: Path, documents: list) -> None:
        raise OSError("Simulated full disk while writing the index")

    monkeypatch.setattr(
        importlib.import_module("pipelines.build"), "create_index", fail_index
    )
    with pytest.raises(OSError, match="full disk"):
        publish(source, archive, source_dir)
    assert current.read_bytes() == before
    with Dataset(source_dir / "published") as reader:
        assert reader.search("ASR 12")[0]["title"] == "ASR 12"
    archive.close()


def test_search_prioritizes_exact_names_and_paginates_documents(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    other_url = URL.replace("karte005", "karte006")
    other = HTML.replace(b"ASR 12</h4>", b"Other system</h4>").replace(
        b"solid state", b"solid state, comparable to ASR 12,"
    )
    archive.add(other_url)
    archive.save(other_url, 200, other, "text/html", {})
    publish(source, archive, source_dir)
    with Dataset(source_dir / "published") as dataset:
        hits = dataset.search("ASR 12")
        assert [hit["title"] for hit in hits] == ["ASR 12", "Other system"]
        assert (
            dataset.search("surveillance", limit=1)[0]["id"]
            != dataset.search("surveillance", limit=1, offset=1)[0]["id"]
        )
        assert len(dataset.search("surveil", prefix=True)) == 2
        with pytest.raises(ValueError, match="Limit"):
            dataset.search("radar", limit=1000)
    archive.close()
