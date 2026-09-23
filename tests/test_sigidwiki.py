import copy
import json
from pathlib import Path

import pytest

from pipelines.archive import Archive
from pipelines.audit import audit
from pipelines.build import publish
from pipelines.distribution import collect_artifacts
from pipelines.media_pipeline import media
from pipelines.model import evidence_id
from pipelines.research import build_research
from pipelines.sources.sigidwiki import (
    DATABASE,
    HEADERS,
    SigIDWiki,
    article_url,
    records,
)
from pipelines.sources.sigidwiki.media import original_url


def row(title: str, *, frequency: str = "161.975 MHz — 162.025 MHz") -> str:
    return f"""<tr><td bgcolor="#DAFFDC"><b><a href="/wiki/{title}">{title}</a></b></td>
    <td>A marine <span class="mw-lingo-tooltip">GMSK<span class="mw-lingo-tooltip-tip">Hidden glossary</span></span> reference.</td>
    <td>{frequency}</td><td>NFM</td><td>GMSK, FSK</td><td>25 kHz</td><td>Worldwide</td>
    <td><audio src="/images/a/ab/sample.wav"></audio></td>
    <td><a href="/wiki/File:Example.png"><img src="/images/thumb/a/ab/Example.png/150px-Example.png"></a></td></tr>"""


def database(*rows: str) -> bytes:
    return (
        "<div id='mw-content-text'><img src='/images/a/ab/banner.png'>"
        "<table class='wikitable'><tr>"
        + "".join(f"<th>{h}</th>" for h in HEADERS)
        + "</tr>"
        + "".join(rows)
        + "</table></div>"
    ).encode()


@pytest.fixture
def body() -> bytes:
    return database(row("AIS"), row("Second_(TEST)"), row("AIS"))


def test_signal_rows_keep_provenance_ranges_status_and_clean_glossary(
    body: bytes,
) -> None:
    source = SigIDWiki()
    entities = source.extract(DATABASE, body, [])
    assert len(entities) == 2
    ais, second = entities
    assert ais.title == "AIS" and ais.kind == "signal"
    assert ais.url == "https://www.sigidwiki.com/wiki/AIS"
    assert "TEST" in second.aliases
    facts = {(f.name, f.raw) for f in ais.facts}
    assert ("Reported frequency range", "161.975 MHz — 162.025 MHz") in facts
    assert ("Frequency", "161.975 MHz") not in facts
    assert ("Modulation", "FSK") in facts and ("Modulation", "GMSK") in facts
    assert ("Signal location", "Worldwide") in facts
    assert ("Signal status", "Active") in facts
    assert (
        "Audio sample URL",
        "https://www.sigidwiki.com/images/a/ab/sample.wav",
    ) in facts
    for entity in entities:
        entity.validate()
        page = entity.evidence[0]
        assert page.url == DATABASE and page.canonical_url == entity.url
        assert page.records and page.record_id == entity.key
        assert "Hidden glossary" not in page.markdown + page.search_text
        assert "banner.png" not in page.markdown
    reordered = source.extract(DATABASE, database(row("Second_(TEST)"), row("AIS")), [])
    assert [e.metadata(source.id) for e in entities] == [
        e.metadata(source.id) for e in reordered
    ]


def test_discovery_stays_on_complete_database_and_rejects_conflicting_duplicates(
    body: bytes,
) -> None:
    source = SigIDWiki()
    assert source.normalize(DATABASE) == DATABASE
    assert source.normalize(DATABASE + "?title=wrong") is None
    assert source.discover(DATABASE, body) == []
    for invalid in [
        b"Access denied",
        database(),
        body.replace(b"Bandwidth", b"Changed column"),
        database(row("AIS"), row("AIS", frequency="10 MHz")),
    ]:
        with pytest.raises(ValueError):
            records(invalid)


@pytest.mark.parametrize(
    "value",
    [
        "https://outside.test/wiki/AIS",
        "/wiki/File:Icon.png",
        "/wiki/../AIS",
        "/wiki/AIS?action=edit",
        "/wiki/AIS#x",
        "https://user@www.sigidwiki.com/wiki/AIS",
    ],
)
def test_rejects_nonarticle_links(value: str) -> None:
    with pytest.raises(ValueError):
        article_url(value)


def test_title_can_contain_a_colon_without_being_a_namespace() -> None:
    assert article_url("/wiki/SOLRAD_7B_(COSPAR_ID:_1965-016D)").endswith(
        "COSPAR_ID%3A_1965-016D)"
    )


def test_signal_claims_are_filterable_without_asserting_channels_or_origin(
    body: bytes,
) -> None:
    source = SigIDWiki()
    entities = [e.metadata(source.id) for e in source.extract(DATABASE, body, [])]
    claims, relations = build_research(entities)
    assert not relations
    assert {c["field"] for c in claims} == {
        "modulation",
        "frequency_range",
        "bandwidth",
        "signal_location",
        "signal_status",
        "reception_mode",
    }
    assert all(c["status"] == "known" for c in claims)
    for claim in claims:
        if claim["field"] == "frequency_range":
            assert claim["value"]["number"]["min"] == 161975000
            assert claim["value"]["number"]["max"] == 162025000


def test_waterfalls_are_originals_associated_with_individual_rows(body: bytes) -> None:
    source = SigIDWiki()
    entities = [e.metadata(source.id) for e in source.extract(DATABASE, body, [])]
    images = source.discover_media(DATABASE, body, entities)
    assert len(images) == 2
    for image, entity in zip(images, entities, strict=True):
        assert image.url == "https://www.sigidwiki.com/images/a/ab/Example.png"
        assert not image.exclusion_reason and image.role == "original"
        assert len(image.references) == 1
        ref = image.references[0]
        assert ref.entity_id == entity["id"]
        assert ref.evidence_id == evidence_id(DATABASE, entity["source_id"])
        assert ref.section == "Waterfall image"
        assert entity["title"] in ref.caption
    with pytest.raises(ValueError, match="retained signal"):
        source.discover_media(DATABASE, body, entities[:1])


@pytest.mark.parametrize(
    "value",
    [
        "https://outside.test/images/a/ab/image.png",
        "/images/thumb/a/ab/image.png",
        "/wiki/File:image.png",
        "/images/a/ab/image.png?x=1",
    ],
)
def test_rejects_unsafe_or_malformed_image_paths(value: str) -> None:
    with pytest.raises(ValueError):
        original_url(value)


def test_database_snapshot_replays_and_audits_row_evidence(
    tmp_path: Path, body: bytes
) -> None:
    source = SigIDWiki()
    source.minimum_entities = 2
    directory = tmp_path / source.id
    archive = Archive(directory / "archives" / "fixture")
    try:
        archive.add(DATABASE)
        archive.save(DATABASE, 200, body, "text/html", {})
        archive.mark_complete(source.id)
        publish(source, archive, directory)
    finally:
        archive.close()
    entities, html, responses = collect_artifacts(tmp_path, [source.id])
    assert len(entities) == len(html) == 2
    assert len(responses) == 1
    assert all(e["evidence"][0]["html_origin"] == "record-rendered" for e in entities)
    result = audit(source, tmp_path)
    assert result["ok"] and result["source_checks"]["verified_records"] == 2
    corrupted = copy.deepcopy(entities)
    corrupted[0]["facts"][0]["raw"] = "Inactive"
    with pytest.raises(ValueError, match="facts differ"):
        source.audit(
            corrupted,
            {DATABASE: body},
            {k.split("/", 1)[1]: v for k, v in html.items()},
        )
    manifest = json.loads((directory / "published/current.json").read_text())
    assert manifest["entities"] == 2
    report = media(source, tmp_path)
    assert report["coverage"]["with_candidates"] == 2
    assert report["coverage"]["multiple_subject_records"] == 1
