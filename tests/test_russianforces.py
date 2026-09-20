import socket
from pathlib import Path

import pytest

from pipelines.archive import Archive
from pipelines.build import build
from pipelines.distribution import collect_artifacts
from pipelines.model import EntityKind, valid_key
from pipelines.sources.russianforces import CATALOG, FEED, RussianForces

BODY = (Path(__file__).parent / "fixtures/russianforces.html").read_bytes()
URL = "https://russianforces.org/blog/2026/01/observations.shtml"
ATOM = f'<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>Observations</title><link rel="alternate" href="{URL}"/></entry></feed>'.encode()


def test_atom_discovery_and_archive_scope() -> None:
    source = RussianForces()
    assert source.discover(FEED, ATOM) == [URL]
    assert source.discover(URL, BODY) == []
    assert source.extract(FEED, ATOM, []) == []
    assert source.normalize(URL + "?tracking=yes#comments") == URL
    assert source.normalize(URL.replace("https://", "http://www.")) == URL
    for url in [
        "https://russianforces.org/blog/archive.shtml",
        "https://russianforces.org/cgi-bin/mt/mt-search.cgi?q=radar",
        "https://russianforces.org/rus/blog/2026/01/post.shtml",
        "https://russianforces.org/data.kmz",
        "https://other.example/blog/2026/01/post.shtml",
        "https://russianforces.org/blog/2026/../post.shtml",
    ]:
        assert source.normalize(url) is None
    for body in [
        b"<feed/>",
        b"<html>Blocked</html>",
        ATOM.replace(URL.encode(), b"https://other.example/post"),
    ]:
        with pytest.raises(ValueError):
            source.discover(FEED, body)


def test_entities_are_named_equipment_with_qualified_shared_evidence() -> None:
    entities = {e.key: e for e in RussianForces().extract(URL, BODY, [])}
    assert set(entities) == {
        "voronezh-dm1",
        "voronezh-dm",
        "sineva",
        "glonass-k",
        "cosmos-9991",
        "cosmos-9992",
        "cosmos-9993",
        "cosmos-9995",
        "cosmos-9998",
    }
    first = entities["voronezh-dm1"]
    assert "current status is uncertain" in first.evidence[0].search_text
    assert "resumed operation" not in first.evidence[0].search_text
    assert "resumed operation" in first.evidence[0].markdown
    assert "Sarmat unrelated" not in first.evidence[0].markdown
    assert "Yars mentioned only" not in first.evidence[0].search_text
    assert all(
        e.evidence[0].markdown == first.evidence[0].markdown for e in entities.values()
    )
    assert entities["sineva"].aliases == ["R-29RM", "Sineva"]
    assert "likely to receive" in entities["cosmos-9991"].evidence[0].search_text
    assert entities["cosmos-9991"].kind == "spacecraft"
    for key, norad in [("cosmos-9992", "12345"), ("cosmos-9993", "12346")]:
        facts = {f.name: f.raw for f in entities[key].facts}
        assert facts["NORAD ID"] == norad
        assert "NORAD ID: " + norad in entities[key].evidence[0].search_text
    assert all(f.evidence == URL for e in entities.values() for f in e.facts)
    positive = BODY.replace(
        b"A road near the city of Bryansk is being repaired.",
        b"The Bryansk submarine completed its overhaul.",
    )
    assert "bryansk" in {e.key for e in RussianForces().extract(URL, positive, [])}


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Loading</html>",
        BODY.replace(b"citation_author", b"missing_author"),
        BODY.replace(b"entry-123", b"entry-invalid"),
    ],
)
def test_malformed_articles_fail_closed(body: bytes) -> None:
    with pytest.raises(ValueError):
        RussianForces().extract(URL, body, [])


def test_unrecognized_prose_does_not_become_an_article_entity() -> None:
    body = b'<meta name="citation_author" content="Author"><meta name="citation_publication_date" content="2026"><div class="entry-asset" id="entry-1"><h1 class="asset-name">Policy discussion</h1><div class="asset-content"><p>General policy and unspecified systems.</p></div></div>'
    assert RussianForces().extract(URL, body, []) == []


def test_catalog_has_unique_safe_ids_and_explicit_aliases() -> None:
    assert len({e["key"] for e in CATALOG}) == len(CATALOG)
    for item in CATALOG:
        assert valid_key(item["key"]) and item["title"] and item["aliases"]
        EntityKind(item["kind"])


def test_offline_merge_retains_all_articles_and_refresh_membership(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = RussianForces()
    source.minimum_entities = 1
    directory = tmp_path / source.id
    archive = Archive(directory / "archives/fixture")
    second_url = URL.replace("observations", "later_report")
    second_body = BODY.replace(b"entry-123", b"entry-124").replace(
        b"January 2", b"January 3"
    )
    for url, body in [(URL, BODY), (second_url, second_body)]:
        archive.add(url)
        archive.save(url, 200, body, "text/html", {})
    archive.mark_complete(source.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction requested network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    build(source, tmp_path, archive_id="fixture")
    records, bodies, _ = collect_artifacts(tmp_path, [source.id])
    assert len(records) == 9 and len(bodies) == 2
    assert all(len(e["evidence"]) == 2 for e in records)
    assert source.discovery_seeds(directory / "archives/next") == sorted(
        [URL, second_url]
    )
    assert next(e for e in records if e["source_id"] == "sineva")["aliases"] == [
        "R-29RM",
        "Sineva",
    ]
