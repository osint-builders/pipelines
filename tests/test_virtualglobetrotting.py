import json
import socket
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipelines.archive import Archive
from pipelines.build import build
from pipelines.distribution import collect_artifacts, content_digest
from pipelines.sources.virtualglobetrotting import FEED, VirtualGlobetrotting

BODY = (Path(__file__).parent / "fixtures/virtualglobetrotting.html").read_bytes()
URL = "https://virtualglobetrotting.com/map/example-radar-station/"
RSS = f"<rss><channel><item><title>Old title</title><link>{URL}</link></item><item><link>{URL}#comments</link></item></channel></rss>".encode()


def test_rss_discovery_is_bounded_to_detail_pages() -> None:
    source = VirtualGlobetrotting()
    assert source.discover(FEED, RSS) == [URL]
    assert source.discover(URL, BODY) == []
    assert source.extract(FEED, RSS, []) == []
    assert source.normalize(URL + "?tracking=x#comments") == URL
    assert source.normalize(URL.replace("https://", "http://www.")) == URL
    for url in [
        "https://other.example/map/example/",
        "https://virtualglobetrotting.com/category/buildings/radar-sites/25/?v=0",
        URL + "nearby/",
        URL + "view/google/",
        "https://virtualglobetrotting.com/search/?q=radar",
        "https://virtualglobetrotting.com/map/../secret/",
    ]:
        assert source.normalize(url) is None
    for body in [
        b"<rss><channel/></rss>",
        b"<html>Blocked</html>",
        RSS.replace(URL.encode(), b"https://other.example/map/example/"),
    ]:
        with pytest.raises(ValueError):
            source.discover(FEED, body)


def test_site_identity_coordinates_and_complete_evidence() -> None:
    source = VirtualGlobetrotting()
    (entity,) = source.extract(URL, BODY, ["Old title"])
    entity.validate()
    assert entity.key == "12345" and entity.kind == "site"
    assert entity.aliases == ["Example Radar Station"]
    assert (
        source.extract(URL.replace("example-radar-station", "renamed"), BODY, [])[0].key
        == entity.key
    )
    facts = {fact.name: fact for fact in entity.facts}
    assert facts["Latitude"].values == [0.0]
    assert facts["Longitude"].values == [-30.25]
    assert facts["Country"].raw == "XX"
    assert all(fact.evidence == URL for fact in entity.facts)
    page = entity.evidence[0]
    assert "old control room" in page.markdown
    assert "old control room" not in page.search_text
    assert "Example Town" in page.search_text
    assert "radome remains visible" in page.search_text
    assert "Celebrity" not in page.markdown and "999 views" not in page.markdown
    assert "unrelated" not in page.markdown and "javascript:" not in page.markdown
    assert "https://reference.example/history" in page.links
    assert "Example Contributor" in page.attribution


def test_valid_sparse_record_retains_location_without_inventing_description() -> None:
    soup = BeautifulSoup(BODY, "html.parser")
    article = soup.select_one(".map-info-description")
    assert article is not None
    article.decompose()
    extra = BeautifulSoup(
        '<div class="map-info-by"><a class="user-link">Sparse Contributor</a></div><meta property="article:published_time" content="2001-01-01T00:00:00Z">',
        "html.parser",
    )
    soup.append(extra)
    (entity,) = VirtualGlobetrotting().extract(URL, str(soup).encode(), [])
    entity.validate()
    assert "radome remains visible" not in entity.evidence[0].search_text
    assert "Example Town" in entity.evidence[0].search_text
    facts = {fact.name: fact.raw for fact in entity.facts}
    assert facts["Contributor"] == "Sparse Contributor"
    assert facts["Published"] == "2001-01-01T00:00:00Z"


@pytest.mark.parametrize(
    "body",
    [
        BODY.replace(b'content="0"', b'content="91"'),
        BODY.replace(b'content="-30.25"', b'content="NaN"'),
        BODY.replace(b"/map/12345/nearby/", b"/map/example/nearby/"),
        BODY.replace(
            b"/category/buildings/radar-sites/", b"/category/buildings/houses/"
        ),
        BODY.replace(b'class="map-info-description"', b'class="missing"'),
    ],
)
def test_incomplete_or_invalid_detail_fails_closed(body: bytes) -> None:
    with pytest.raises(ValueError):
        VirtualGlobetrotting().extract(URL, body, [])


def test_offline_publication_and_feed_rolloff_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = VirtualGlobetrotting()
    source.minimum_entities = 1
    directory = tmp_path / source.id
    archive = Archive(directory / "archives/first")
    for url, body, content_type in [
        (FEED, RSS, "application/xml"),
        (URL, BODY, "text/html"),
    ]:
        archive.add(url)
        archive.save(url, 200, body, content_type, {})
    archive.mark_complete(source.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction requested network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    build(source, tmp_path, archive_id="first")
    entities, bodies, _ = collect_artifacts(tmp_path, [source.id])
    assert len(entities) == 1 and next(iter(bodies.values())) == BODY
    assert entities[0]["id"] == source.id + ":12345"
    second = directory / "archives/second"
    assert source.discovery_seeds(second) == [URL]
    monkeypatch.setattr("pipelines.sources.feeds.load_snapshot", forbidden)
    assert source.discovery_seeds(second) == [URL]
    cache = second / "discovery/previous-urls.json"
    record = json.loads(cache.read_text())
    record["urls"].append(URL.replace("station", "new"))
    cache.write_text(json.dumps(record))
    with pytest.raises(ValueError, match="checksum"):
        source.discovery_seeds(second)


def test_view_counters_do_not_change_the_dataset_fingerprint() -> None:
    source = VirtualGlobetrotting()
    first = source.extract(URL, BODY, [])[0].metadata(source.id)
    second = source.extract(URL, BODY.replace(b"999 views", b"1000 views"), [])[
        0
    ].metadata(source.id)
    assert content_digest([first]) == content_digest([second])
