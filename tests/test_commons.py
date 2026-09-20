import json
import socket
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipelines.archive import Archive
from pipelines.build import build
from pipelines.distribution import collect
from pipelines.sources.commons import ORIGIN, Commons, english

FILE_URL = ORIGIN + "/wiki/File:Example.jpg"
BODY = (Path(__file__).parent / "fixtures/commons.html").read_bytes()


def category(key: int, title: str, children: tuple[str, ...] = ()) -> tuple[str, bytes]:
    url = ORIGIN + "/wiki/Category:" + title.replace(" ", "_")
    links = "".join(
        f'<div class="CategoryTreeItem"><a href="{href}">Member</a></div>'
        for href in children
        if "Category:" in href
    )
    files = "".join(
        f'<div class="gallerytext"><a href="{href}">Photo</a></div>'
        for href in children
        if "File:" in href
    )
    body = f'''<html><head><meta charset="utf-8"><link rel="canonical" href="{url}"><link rel="license" href="https://creativecommons.org/licenses/by-sa/4.0/">
<script>RLCONF={{"wgArticleId":{key},"wgRevisionId":8001,"wgNamespaceNumber":14}};</script></head><body><h1 id="firstHeading">Category:{title}</h1>
<div id="mw-content-text"><div class="mw-parser-output"><div style="display:none">Unrelated hidden aliases</div><div class="description" lang="en">English equipment description with qualifications.</div><div class="description" lang="ru">Other language description.</div>
<table id="wdinfobox"><tr><td id="wdinfoboxcaption">{title} label</td></tr><tr><th class="wikidatainfobox-lcell">Instance of</th><td>Test equipment</td></tr><tr id="wdinfo_ac"><td>Unrelated authority navigation</td></tr></table></div>
<div id="mw-subcategories">{links}</div><div id="mw-category-media">{files}</div></div><a href="/wiki/Category:Unrelated">Parent outside crawl scope</a></body></html>'''
    return url, body.encode()


def prepared() -> tuple[Commons, dict[str, bytes]]:
    source = Commons()
    titles = {
        1: "Root",
        2: "Example",
        3: "Example B",
        4: "Example B in a museum",
        5: "Command vehicle",
        6: "General photos",
    }
    policies = {
        1: "collection",
        2: "entity",
        3: "entity",
        4: "context",
        5: "entity",
        6: "collection",
    }
    source.catalog = {
        str(k): {
            "title": v,
            "role": policies[k],
            "kind": "vehicle" if k == 5 else "radar",
        }
        for k, v in titles.items()
    }
    source.minimum_entities = 3
    urls = {key: category(key, name)[0] for key, name in titles.items()}
    source.seeds = (urls[1],)
    generic = ORIGIN + "/wiki/File:Generic.jpg"
    members = {
        1: (urls[2], urls[5], generic),
        2: (urls[3], urls[6], FILE_URL),
        3: (urls[4], FILE_URL),
        4: (FILE_URL,),
        5: (FILE_URL,),
        6: (generic,),
    }
    pages = dict(category(key, name, members[key]) for key, name in titles.items())
    pages[FILE_URL] = BODY
    pages[generic] = BODY.replace(b"Example.jpg", b"Generic.jpg")
    source.prepare(pages.items())
    return source, pages


def test_scope_navigation_and_pagination() -> None:
    source, pages = prepared()
    root = source.seeds[0]
    assert len(source.discover(root, pages[root])) == 3
    assert source.discover(FILE_URL, BODY) == []
    assert source.normalize(FILE_URL + "#Summary") == FILE_URL
    assert (
        source.normalize(ORIGIN + "/wiki/Category:Example%20B")
        == ORIGIN + "/wiki/Category:Example_B"
    )
    assert (
        source.normalize(ORIGIN + "/wiki/File:Радар.jpg")
        == ORIGIN + "/wiki/File:%D0%A0%D0%B0%D0%B4%D0%B0%D1%80.jpg"
    )
    for url in [
        "https://en.wikipedia.org/wiki/Category:Example",
        "https://upload.wikimedia.org/example.jpg",
        ORIGIN + "/wiki/User:Example",
        ORIGIN + "/wiki/Special:Search",
        ORIGIN + "/wiki/Category:",
        ORIGIN + "/w/api.php?action=query",
        FILE_URL + "?action=edit",
        FILE_URL + "?filefrom=T",
        root + "?filefrom=T&filefrom=Z",
        root + "?uselang=ru",
        ORIGIN + "/wiki/File:../private",
    ]:
        assert source.normalize(url) is None
    next_page = b'<a href="/wiki/Category:Root?filefrom=T">next page</a>'
    paginated = pages[root].replace(
        b'<div id="mw-category-media">', b'<div id="mw-category-media">' + next_page
    )
    assert root + "?filefrom=T" in source.discover(root, paginated)
    with pytest.raises(ValueError, match="pagination"):
        source.discover(
            root,
            paginated.replace(
                b"/wiki/Category:Root?filefrom=T",
                b"/w/index.php?title=Category:Root&amp;filefrom=T",
            ),
        )
    with pytest.raises(ValueError, match="membership"):
        source.discover(root, b"<html>Loading</html>")


def test_membership_groups_photos_and_keeps_specific_variants() -> None:
    source, pages = prepared()
    entities = source.extract(FILE_URL, BODY, [])
    assert [e.key for e in entities] == ["3", "5"]
    assert [e.kind for e in entities] == ["radar", "vehicle"]
    assert source.owners[ORIGIN + "/wiki/Category:Example_B_in_a_museum"] == {"3"}
    assert source.extract(ORIGIN + "/wiki/File:Generic.jpg", BODY, []) == []
    assert source.extract(source.seeds[0], pages[source.seeds[0]], []) == []
    assert entities[0].evidence[0].markdown == entities[1].evidence[0].markdown
    assert entities[0].aliases == ["Example B", "Example B label"]
    assert "Unrelated hidden aliases" not in entities[0].aliases
    for entity in entities:
        entity.validate()


def test_full_evidence_attribution_and_selected_english_search() -> None:
    source, _ = prepared()
    entity = source.extract(FILE_URL, BODY, ["filename is not identity"])[0]
    page = entity.evidence[0]
    assert page.language == "mul"
    assert (
        "Исходное описание" in page.markdown
        and "Исходное описание" not in page.search_text
    )
    assert "Identification is tentative" in page.search_text
    assert (
        "Give credit" not in page.search_text and "File history" not in page.search_text
    )
    assert "File history" in page.markdown and "Example camera" in page.markdown
    assert (
        "disputed identification" in page.markdown
        and "source reference" in page.markdown
    )
    assert "oldid=9001" in page.attribution and "action=history" in page.attribution
    assert "converted to Markdown" in page.attribution
    assert "https://upload.wikimedia.org/example.jpg" in page.links
    assert ORIGIN + "/wiki/User:Example" in page.links
    facts = {fact.name: fact for fact in entity.facts}
    assert facts["Page text license"].raw.endswith("by-sa/4.0/")
    assert "CC BY 3.0" in facts["Media license"].raw
    assert facts["File Date"].raw == "23 August 2009"
    assert "not equipment" in str(facts["File Date"].qualifier)
    assert "Misleading filename" not in page.markdown
    node = BeautifulSoup('<div lang="ru">Not English</div>', "html.parser").div
    assert node is not None and english(node) == ""


def test_category_description_retained_without_navigation_in_search() -> None:
    source, pages = prepared()
    url = ORIGIN + "/wiki/Category:Example_B"
    entity = source.extract(url, pages[url], [])[0]
    page = entity.evidence[0]
    assert "English equipment description" in page.search_text
    assert "Other language description" in page.markdown
    assert "Other language description" not in page.search_text
    assert "Unrelated authority navigation" not in page.search_text
    assert "Unrelated hidden aliases" not in page.markdown
    assert any(
        f.name == "Instance of" and f.raw == "Test equipment" for f in entity.facts
    )


def test_subject_url_survives_context_pages_encountered_first() -> None:
    source, pages = prepared()
    context = ORIGIN + "/wiki/Category:Example_B_in_a_museum"
    subject = ORIGIN + "/wiki/Category:Example_B"
    first = source.extract(context, pages[context], [])[0]
    first.merge(source.extract(subject, pages[subject], [])[0])
    first.merge(source.extract(FILE_URL, BODY, [])[0])
    assert first.metadata(source.id)["url"] == subject
    assert len(first.evidence) == 3


def test_unreviewed_or_missing_categories_fail_closed() -> None:
    source, pages = prepared()
    source.catalog.pop("3")
    with pytest.raises(ValueError, match="Unreviewed"):
        source.prepare(pages.items())
    source, pages = prepared()
    pages.pop(ORIGIN + "/wiki/Category:Example_B")
    with pytest.raises(ValueError, match="Missing Commons category"):
        source.prepare(pages.items())
    with pytest.raises(ValueError, match="prepared"):
        Commons().extract(FILE_URL, BODY, [])


def test_context_cycle_fails_instead_of_recursing_forever() -> None:
    source, _ = prepared()
    source.catalog["7"] = {"title": "Other context", "role": "context"}
    first = ORIGIN + "/wiki/Category:Example_B_in_a_museum"
    second = ORIGIN + "/wiki/Category:Other_context"
    pages = dict(
        [
            category(4, "Example B in a museum", (second,)),
            category(7, "Other context", (first,)),
        ]
    )
    with pytest.raises(ValueError, match="Cycle"):
        source.prepare(pages.items())


def test_legacy_prose_and_category_context_descriptions() -> None:
    source, pages = prepared()
    soup = BeautifulSoup(BODY, "html.parser")
    parser = soup.select_one(".mw-parser-output")
    assert parser is not None
    parser.clear()
    parser.append(
        BeautifulSoup(
            '<h2>Summary</h2><p>Radar photographed on a ship; identification disputed.</p><ul><li>Description: fire control antenna</li></ul><h2 id="Licensing">Licensing</h2><p>Boilerplate must stay out of search.</p>',
            "html.parser",
        )
    )
    evidence = source.extract(FILE_URL, str(soup).encode(), [])[0].evidence[0]
    assert "identification disputed" in evidence.search_text
    assert "fire control antenna" in evidence.search_text
    assert (
        "Boilerplate" not in evidence.search_text and "Boilerplate" in evidence.markdown
    )
    context = ORIGIN + "/wiki/Category:Example_B_in_a_museum"
    body = pages[context].replace(
        b'<div class="mw-parser-output">',
        b'<div class="mw-parser-output"><p>Museum nickname is explicitly documented here.</p>',
    )
    assert (
        "Museum nickname"
        in source.extract(context, body, [])[0].evidence[0].search_text
    )


@pytest.mark.parametrize(
    "body",
    [
        BODY.replace(b'"wgArticleId":7001', b'"wgArticleId":"7001"'),
        BODY.replace(b'"wgRevisionId":9001', b'"wgRevisionId":0'),
        BODY.replace(b'"wgNamespaceNumber":6', b'"wgNamespaceNumber":7'),
        BODY.replace(b'"mw-content-text"', b'"missing"'),
        BODY.replace(b'"mw-parser-output"', b'"missing"'),
        BODY.replace(b"by-sa/4.0", b"unknown/4.0"),
    ],
)
def test_incomplete_file_content_or_provenance_fails(body: bytes) -> None:
    source, _ = prepared()
    with pytest.raises(ValueError):
        source.extract(FILE_URL, body, [])


def test_offline_preparation_publication_and_byte_exact_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, pages = prepared()
    source.ready = False
    archive = Archive(tmp_path / source.id / "archives/fixture")
    for url, body in pages.items():
        archive.add(url)
        archive.save(url, 200, body, "text/html", {})
    archive.mark_complete(source.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    snapshot = build(source, tmp_path, archive_id="fixture")
    entities, bodies = collect(tmp_path, [source.id])
    assert [e["id"] for e in entities] == ["commons:2", "commons:3", "commons:5"]
    assert len(entities[1]["evidence"]) == 3
    assert BODY in bodies.values()
    manifest = json.loads((snapshot / "manifest.json").read_text())
    assert manifest["entities"] == 3 and manifest["evidence_pages"] == 5
    assert len(manifest["excluded_from_entities"]) == 3


def test_reviewed_catalog_is_complete_and_has_valid_roles() -> None:
    source = Commons()
    assert len(source.catalog) == 196
    assert sum(c["role"] == "entity" for c in source.catalog.values()) == 151
    assert source.catalog["67286655"]["role"] == "collection"
    assert source.catalog["64867255"]["kind"] == "emitter"
    assert source.catalog["46460643"]["kind"] == "site"
