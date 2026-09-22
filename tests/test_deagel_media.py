from copy import deepcopy
from pathlib import Path

from pipelines.sources.base import MediaSource
from pipelines.sources.deagel import Deagel

URL = "https://www.deagel.com/Armies/Example/a000001"
BODY = Path(__file__).with_name("fixtures").joinpath("deagel.html").read_bytes()


def test_variant_photos_do_not_leak_to_sibling_variants_or_news() -> None:
    body = (
        BODY.replace(b"library/sample.jpg", b"library/sm/2020/example.jpg")
        .replace(
            b"<h4>News</h4>",
            b'<h4>News</h4><div><img src="library/sm/2020/news.jpg"></div>',
        )
        .replace(
            b'<a href="Photo/example">An image caption</a>',
            b"""<div><a href="photo/example"><img src="library/sm/2020/example.jpg"><p class="card-text">Example B photograph</p></a><a href="photo/unknown"><img src="library/sm/2020/unknown.jpg"><p class="card-text">Unidentified family variant</p></a></div>""",
        )
    )
    source = Deagel()
    assert isinstance(source, MediaSource)
    extracted = source.extract(URL, body, [])
    entities = [entity.metadata(source.id) for entity in extracted]
    before = deepcopy(entities)
    rows = list(source.discover_media(URL, body, entities))
    eligible = [row for row in rows if not row.exclusion_reason]
    assert len(eligible) == 2
    assert all(row.url.endswith("/library/sm/2020/example.jpg") for row in eligible)
    assert all(
        row.role == "preview" and row.original_url == row.url for row in eligible
    )
    assert all(
        [ref.entity_id for ref in row.references] == ["deagel:a000001-002"]
        for row in eligible
    )
    assert all(row.caption == "Example B photograph" for row in eligible)
    assert {row.exclusion_reason for row in rows if row.exclusion_reason} == {
        "related_news_image",
        "ambiguous_variant_image",
    }
    assert not any(row.references for row in rows if row.exclusion_reason)
    assert entities == before
    assert source.extract(URL, body, []) == extracted


def test_catalog_flags_and_navigation_are_explicit_exclusions() -> None:
    body = b'<nav><img src="img/3a3.png"></nav><main><div class="guide"><img src="img/flags/example.png"></div></main>'
    rows = list(Deagel().discover_media("https://www.deagel.com/Armies", body, []))
    assert [row.exclusion_reason for row in rows] == [
        "navigation_image",
        "country_flag",
    ]
    assert not any(row.references for row in rows)


def test_single_variant_gallery_keeps_actual_source_caption() -> None:
    body = b"""<main><div><h1>Radar</h1><a id="001"></a><h1>Radar A</h1><div></div>
    <h4>Photo Gallery</h4><div><a href="photo/x"><img src="library/sm/2020/x.jpg"><p class="card-text">Transport configuration</p></a></div></div></main>"""
    rows = list(
        Deagel().discover_media(
            URL,
            body,
            [{"id": "deagel:a000001-001", "evidence": [{"id": "ev", "url": URL}]}],
        )
    )
    assert len(rows) == 1 and not rows[0].exclusion_reason
    assert rows[0].caption == "Transport configuration"
    assert rows[0].references[0].evidence_id == "ev"
