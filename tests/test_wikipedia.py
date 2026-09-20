import json
import socket
from pathlib import Path

import pytest

from pipelines.archive import Archive
from pipelines.build import build
from pipelines.distribution import collect_artifacts
from pipelines.sources.wikipedia import ORIGIN, Wikipedia

BODY = (Path(__file__).parent / "fixtures/wikipedia.html").read_bytes()
URL = ORIGIN + "/wiki/Example_radar"
CATEGORY = b'<div id="mw-pages"><div class="mw-category-group"><a href="/wiki/Example_radar">Example radar</a></div></div><div id="mw-subcategories"><div class="CategoryTreeItem"><a href="/wiki/Category:Child">Child</a></div></div><a href="/wiki/Unrelated">Outside membership</a>'


def test_scope_membership_and_pagination() -> None:
    source = Wikipedia()
    assert source.discover(source.seeds[0], CATEGORY) == [
        ORIGIN + "/wiki/Category:Child",
        URL,
    ]
    assert source.labels(source.seeds[0], CATEGORY) == {URL: ["Example radar"]}
    assert source.discover(URL, BODY) == []
    assert source.normalize(URL + "#Specification") == URL
    assert source.normalize(ORIGIN + "/wiki/Example%20radar") == URL
    for target in [
        "https://zh.wikipedia.org/wiki/Example_radar",
        ORIGIN + "/wiki/Special:Search",
        ORIGIN + "/wiki/Talk:Example_radar",
        ORIGIN + "/wiki/File:Example.jpg",
        ORIGIN + "/w/api.php?action=query",
        ORIGIN + "/w/index.php?title=Example_radar&oldid=1",
        URL + "?action=edit",
        ORIGIN + "/wiki/../private",
    ]:
        assert source.normalize(target) is None
    paginated = CATEGORY.replace(
        b'<div id="mw-pages">',
        b'<div id="mw-pages"><a href="/wiki/Category:Military_radars_of_China?pagefrom=Z">next page</a>',
    )
    assert source.seeds[0] + "?pagefrom=Z" in source.discover(
        source.seeds[0], paginated
    )
    blocked = paginated.replace(
        b"/wiki/Category:Military_radars_of_China?pagefrom=Z",
        b"/w/index.php?title=Category:Military_radars_of_China&amp;pagefrom=Z",
    )
    with pytest.raises(ValueError, match="pagination"):
        source.discover(source.seeds[0], blocked)
    with pytest.raises(ValueError, match="membership"):
        source.discover(source.seeds[0], b"<html>Blocked</html>")


def test_article_identity_aliases_qualifiers_and_full_evidence() -> None:
    entity = Wikipedia().extract(URL, BODY, ["Example radar"])[0]
    entity.validate()
    assert entity.key == "1234" and entity.kind == "radar"
    assert entity.aliases == ["Dragon Sample", "Example 12", "Example radar"]
    facts = {f.name: f.raw for f in entity.facts}
    assert facts["Range"] == "250 km (Example 12B)"
    assert facts["Target"] == "5 m ^(2)"
    assert facts["Wikipedia revision ID"] == "9876"
    page = entity.evidence[0]
    assert "oldid=9876" in page.attribution and "action=history" in page.attribution
    assert (
        "CC BY-SA" in page.attribution and "converted to Markdown" in page.attribution
    )
    assert (
        "Research citation" in page.markdown
        and "Historical book details" in page.markdown
    )
    assert "Some statements lack reliable sources" in page.markdown
    assert "believed" in page.markdown and "believed" in page.search_text
    assert "Research citation" not in page.search_text
    assert "Historical book details" not in page.search_text
    assert "Other unrelated equipment" not in page.search_text
    assert "Unrelated navigation equipment" not in page.markdown
    assert "https://upload.wikimedia.org/example.jpg" in page.markdown
    assert "https://example.org/book" in page.links
    assert Wikipedia().extract(URL, BODY, []) == []


def test_redirect_is_target_identity_not_a_false_alias() -> None:
    body = BODY.replace(b"Example radar", b"Example aircraft").replace(
        b'class="infobox"', b'class="infobox ib-aircraft"'
    )
    entity = Wikipedia().extract(
        ORIGIN + "/wiki/Old_radar_name", body, ["Old radar name"]
    )[0]
    assert entity.key == "1234" and entity.kind == "aircraft"
    assert "Old radar name" not in entity.aliases
    assert entity.evidence[0].url == ORIGIN + "/wiki/Old_radar_name"
    assert next(f.raw for f in entity.facts if f.name == "Canonical article") == URL


def test_unknown_category_member_requires_classification() -> None:
    with pytest.raises(ValueError, match="Unreviewed Wikipedia entity kind"):
        Wikipedia().extract(URL, BODY.replace(b"radar", b"device"), ["Example device"])


def test_legacy_article_sections_and_nonitems() -> None:
    body = (
        BODY.replace(b'<section data-mw-section-id="0">', b"")
        .replace(b'<section data-mw-section-id="1">', b"")
        .replace(b'<section data-mw-section-id="2">', b"")
        .replace(b'<section data-mw-section-id="3">', b"")
        .replace(b"</section>", b"")
    )
    entity = Wikipedia().extract(URL, body, ["Example radar"])[0]
    assert "Frequency: S band" in entity.evidence[0].search_text
    assert "Historical book details" not in entity.evidence[0].search_text
    excluded = BODY.replace(b'"wgArticleId":1234', b'"wgArticleId":82932384')
    assert Wikipedia().extract(URL, excluded, ["Institute"]) == []


@pytest.mark.parametrize(
    "body",
    [
        b"<html>Loading</html>",
        BODY.replace(b'"wgRevisionId":9876', b'"wgRevisionId":0'),
        BODY.replace(b'"wgNamespaceNumber":0', b'"wgNamespaceNumber":1'),
        BODY.replace(b'"wgArticleId":1234', b'"wgArticleId":"1234"'),
        BODY.replace(b'rel="license"', b'rel="missing"'),
        BODY.replace(b"by-sa/4.0", b"unknown/4.0"),
        BODY.replace(b'id="mw-content-text"', b'id="missing"'),
    ],
)
def test_missing_content_or_provenance_fails_closed(body: bytes) -> None:
    with pytest.raises(ValueError):
        Wikipedia().extract(URL, body, ["Example radar"])


def test_offline_category_publication_and_exact_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Wikipedia()
    source.minimum_entities = 1
    archive = Archive(tmp_path / source.id / "archives/fixture")
    for url, body in [(source.seeds[0], CATEGORY), (URL, BODY)]:
        archive.add(url)
        archive.save(url, 200, body, "text/html", {})
    archive.mark_complete(source.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    path = build(source, tmp_path, archive_id="fixture")
    entities, bodies, _ = collect_artifacts(tmp_path, [source.id])
    assert entities[0]["id"] == "wikipedia:1234"
    assert list(bodies.values()) == [BODY]
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["entities"] == 1 and manifest["evidence_pages"] == 1
