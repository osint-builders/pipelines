import base64
import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest
from scrapy import Request
from scrapy.http import Response

from pipelines.archive import Archive
from pipelines.build import publish
from pipelines.crawl import ArchiveSpider
from pipelines.distribution import collect_artifacts, content_digest
from pipelines.sources.fandom import (
    API,
    CATEGORY,
    RIGHTS,
    SEED,
    Fandom,
    api,
    article_url,
)

HTML = """<div class="mw-parser-output"><div class="thumb"><p><b>Unrelated caption</b></p></div>
<p id="coordinates">Coordinates: <b>Other place</b></p>
<table class="infobox"><tr><th>Range</th><td>Up to 250 km, depending on mode</td></tr></table>
<p>The <b>Example radar</b>, also called <b>Test Eye</b>, is a surveillance radar. It detects small airborne targets. Unlike <b>Other radar</b>, its reported range is uncertain.</p>
<h2>Design<span class="mw-editsection">[edit]</span></h2><p>Operates on a mobile carrier.</p>
<h2>References</h2><ol class="references"><li><a href="https://example.test/report">Original report</a></li></ol>
<p>All or a portion of this article comes from <a href="https://en.wikipedia.org/wiki/Example">Wikipedia</a>, licensed under GFDL. <a href="https://en.wikipedia.org/w/index.php?title=Example&amp;action=history">Edit history</a>.</p>
<div class="navbox">Unrelated navigation</div></div>"""


def article(key: str = "1", title: str = "Example radar") -> bytes:
    return json.dumps(
        {
            "parse": {
                "pageid": int(key),
                "title": title,
                "revid": 99,
                "text": {"*": HTML},
                "categories": [{"*": "Russian_radars"}],
            }
        }
    ).encode()


def category(keys: tuple[str, ...] = ("1",), /, **extra: object) -> bytes:
    return json.dumps(
        {
            "query": {
                "categorymembers": [
                    {
                        "pageid": int(k),
                        "title": "Example radar" if k == "1" else "Alternate article",
                        "ns": 0,
                    }
                    for k in keys
                ]
            },
            **extra,
        }
    ).encode()


def source() -> Fandom:
    adapter = Fandom()
    adapter.minimum_entities = 1
    adapter.catalog = {
        "1": {
            "title": "Example radar",
            "entity": "1",
            "kind": "radar",
            "aliases": ["Test Eye", "Absent alias"],
        }
    }
    return adapter


def pages() -> dict[str, bytes]:
    return {
        SEED: category(),
        RIGHTS: json.dumps(
            {
                "query": {
                    "rightsinfo": {
                        "text": "CC-BY-SA",
                        "url": "https://www.fandom.com/licensing",
                    }
                }
            }
        ).encode(),
        article_url("1"): article(),
    }


def test_public_api_scope_and_pagination() -> None:
    adapter = source()
    next_url = api(
        action="query",
        list="categorymembers",
        cmtitle=CATEGORY,
        cmlimit="500",
        cmcontinue="page|NEXT|1",
        **{"continue": "-||"},
    )
    body = category(**{"continue": {"cmcontinue": "page|NEXT|1", "continue": "-||"}})
    assert set(adapter.discover(SEED, body)) == {article_url("1"), next_url}
    assert adapter.labels(SEED, body) == {article_url("1"): ["Example radar"]}
    for url in [SEED, RIGHTS, article_url("1"), next_url]:
        assert adapter.normalize(url) == url
    for url in [
        API + "?action=edit&format=json",
        SEED + "&cmlimit=500",
        api(
            action="query",
            list="categorymembers",
            cmtitle="Category:Other",
            cmlimit="500",
        ),
        SEED.replace("military-history", "other"),
        article_url("1").replace("pageid=1", "pageid=-1"),
        RIGHTS.replace("https://", "https://user@"),
        API.replace("api.php", "wiki/Example"),
    ]:
        assert adapter.normalize(url) is None
    with pytest.raises(ValueError, match="continuation"):
        adapter.category(category(**{"continue": {"other": "x"}}))
    with pytest.raises(ValueError, match="API response"):
        adapter.discover(article_url("1"), b'{"error":{"code":"missingtitle"}}')
    with pytest.raises(ValueError, match="category member"):
        adapter.category(category().replace(b'"ns": 0', b'"ns": 14'))


def test_rendered_article_retains_full_evidence_with_focused_search() -> None:
    adapter = source()
    adapter.prepare(pages().items())
    entity = adapter.extract(article_url("1"), article(), [])[0]
    evidence = entity.evidence[0]
    assert entity.key == "1" and entity.url.endswith("/wiki/Example_radar")
    assert entity.aliases == ["Test Eye"]
    assert evidence.html_sha256 == ""
    assert HTML in evidence.rendered_html
    assert "GFDL" in evidence.markdown and "Edit history" in evidence.markdown
    assert "Original report" in evidence.markdown
    assert (
        "Original report" not in evidence.search_text
        and "GFDL" not in evidence.search_text
    )
    assert "Operates on a mobile carrier" in evidence.search_text
    assert "Unrelated navigation" not in evidence.markdown
    assert entity.facts[-1].raw == "Up to 250 km, depending on mode"
    assert not entity.facts[-1].values
    assert "CC-BY-SA" in evidence.attribution and "4.0" not in evidence.attribution


def test_reviewed_duplicates_merge_but_unreviewed_pages_and_missing_pages_fail() -> (
    None
):
    adapter = source()
    adapter.catalog["2"] = {
        "title": "Alternate article",
        "entity": "1",
        "kind": "radar",
    }
    captures = pages() | {
        SEED: category(("1", "2")),
        article_url("2"): article("2", "Alternate article"),
    }
    adapter.prepare(captures.items())
    entity = adapter.extract(article_url("2"), captures[article_url("2")], [])[0]
    entity.merge(adapter.extract(article_url("1"), article(), [])[0])
    assert (
        entity.title == "Example radar"
        and entity.url.endswith("/Example_radar")
        and len(entity.evidence) == 2
    )
    with pytest.raises(ValueError, match="identity catalog"):
        source().prepare(captures.items())
    captures.pop(article_url("2"))
    with pytest.raises(ValueError, match="Incomplete Fandom article"):
        adapter.prepare(captures.items())


@pytest.mark.parametrize(
    "change", ["identity", "revision", "body", "license", "continuation"]
)
def test_incomplete_or_changed_responses_fail_closed(change: str) -> None:
    captures = pages()
    adapter = source()
    if change == "license":
        captures.pop(RIGHTS)
    elif change == "continuation":
        captures[SEED] = category(**{"continue": {"cmcontinue": "next"}})
    else:
        data = json.loads(article())
        if change == "identity":
            data["parse"]["pageid"] = 2
        elif change == "revision":
            data["parse"]["revid"] = 0
        else:
            data["parse"]["text"]["*"] = "<p>Challenge</p>"
        captures[article_url("1")] = json.dumps(data).encode()
    with pytest.raises(ValueError):
        adapter.prepare(captures.items())
        adapter.extract(article_url("1"), captures[article_url("1")], [])


def test_api_archive_offline_publish_exact_exports_and_content_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction opened the network")

    monkeypatch.setattr(socket, "socket", no_network)
    adapter = source()
    directory = tmp_path / adapter.id
    archive = Archive(directory / "archives/fixture")
    try:
        for url, body in pages().items():
            archive.add(url)
            archive.save(url, 200, body, "application/json; charset=utf-8", {})
        assert all(row["file"].endswith(".json") for row in archive.pages("saved"))
        archive.mark_complete(adapter.id)
        snapshot = publish(adapter, archive, directory)
        entities, html, _ = collect_artifacts(tmp_path, [adapter.id])
        page = entities[0]["evidence"][0]
        assert base64.b64decode(page["source_response"]["body_base64"]) == article()
        assert html["fandom/" + page["id"]].startswith(b"<!doctype html>")
        assert (
            "Source: https://military-history.fandom.com/wiki/Example_radar"
            in page["markdown"]
        )
        changed = deepcopy(entities)
        changed[0]["evidence"][0]["source_response"]["body_base64"] = (
            "transport changed"
        )
        assert content_digest(changed) == content_digest(entities)
        changed[0]["evidence"][0]["markdown"] += "Changed description"
        assert content_digest(changed) != content_digest(entities)
        (snapshot / "html" / f"{page['id']}.html").write_text("Corrupted HTML")
        with pytest.raises(ValueError, match="HTML checksum"):
            collect_artifacts(tmp_path, [adapter.id])
        publish(adapter, archive, directory)
        raw_page = next(
            p for p in archive.pages("saved") if p["url"] == article_url("1")
        )
        (archive.path / raw_page["file"]).write_bytes(b"Changed API JSON")
        with pytest.raises(ValueError, match="API response checksum"):
            collect_artifacts(tmp_path, [adapter.id])
    finally:
        archive.close()


def test_thumbnail_failure_does_not_change_search_or_content_identity() -> None:
    adapter = source()
    adapter.prepare(pages().items())
    good = '<span typeof="mw:File"><a href="https://images.test/temporary"><img data-image-name="Example image.jpg" src="https://images.test/thumbnail"></a></span><div>Image caption</div>'
    broken = '<span typeof="mw:Error mw:File"><a href="/wiki/File:Example_image.jpg" title="File:Example image.jpg"><span class="mw-broken-media">Error creating thumbnail:</span></a></span><div>Image caption</div>'
    records = []
    for fragment in [good, broken]:
        data = json.loads(article())
        data["parse"]["text"]["*"] = HTML.replace(
            '<div class="thumb">', fragment + '<div class="thumb">'
        )
        if fragment == broken:
            data["parse"]["categories"].append({"*": "Pages_with_broken_file_links"})
        entity = adapter.extract(article_url("1"), json.dumps(data).encode(), [])[0]
        assert "Image caption" in entity.evidence[0].markdown
        assert "File:Example_image.jpg" in entity.evidence[0].markdown
        assert "Error creating thumbnail" not in entity.evidence[0].search_text
        record = entity.metadata("fandom")
        record["evidence"][0].pop("rendered_html")
        records.append(record)
    assert records[0] == records[1]


def test_complete_pagination_and_cycle_detection() -> None:
    adapter = source()
    adapter.catalog["2"] = {
        "title": "Alternate article",
        "entity": "2",
        "kind": "radar",
    }
    continuation = {"cmcontinue": "next", "continue": "-||"}
    first = category(**{"continue": continuation})
    next_url = adapter.category(first)[1]
    assert next_url is not None
    captures = pages() | {
        SEED: first,
        next_url: category(("2",)),
        article_url("2"): article("2", "Alternate article"),
    }
    adapter.prepare(captures.items())
    assert len(adapter.members) == 2
    captures[next_url] = category(("2",), **{"continue": continuation})
    with pytest.raises(ValueError, match="Cyclic"):
        adapter.prepare(captures.items())


def test_api_error_with_http_200_remains_retryable(tmp_path: Path) -> None:
    adapter = source()
    archive = Archive(tmp_path / "archive")
    try:
        url = article_url("1")
        archive.add(url)
        spider = ArchiveSpider(adapter, archive)
        request = Request(url, meta={"archive_url": url})
        error = Response(
            url,
            status=200,
            headers={"Content-Type": "application/json"},
            body=b'{"error":{"code":"maxlag"}}',
            request=request,
        )
        with pytest.raises(ValueError, match="API response"):
            list(spider.capture(error))
        assert len(archive.pages("failed")) == 1
        good = Response(
            url,
            status=200,
            headers={"Content-Type": "application/json"},
            body=article(),
            request=request,
        )
        assert list(spider.capture(good)) == []
        assert not archive.pages("failed") and len(archive.pages("saved")) == 1
    finally:
        archive.close()
