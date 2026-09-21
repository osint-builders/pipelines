import json

import pytest

from pipelines.media import MediaCandidate
from pipelines.sources.base import MediaSource
from pipelines.sources.fandom import Fandom
from pipelines.sources.wikipedia import Wikipedia

WIKI = "https://en.wikipedia.org/wiki/Radar"
FANDOM = "https://military-history.fandom.com/api.php?action=parse&pageid=1"
ORIGINAL = "https://upload.wikimedia.org/wikipedia/commons/a/ab/Radar.jpg"
PREVIEW = (
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Radar.jpg/250px-Radar.jpg"
)
LARGE = (
    "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Radar.jpg/500px-Radar.jpg"
)


def discover(
    source: Wikipedia | Fandom,
    html: str,
    *,
    entities: list[dict] | None = None,
) -> list[MediaCandidate]:
    url = WIKI if source.id == "wikipedia" else FANDOM
    if entities is None:
        entities = [{"id": f"{source.id}:1", "evidence": [{"id": "page", "url": url}]}]
    body = '<div class="mw-parser-output">' + html + "</div>"
    if source.id == "fandom":
        body = json.dumps({"parse": {"text": {"*": body}}})
    return source.discover_media(url, body.encode(), entities)


@pytest.mark.parametrize("source", [Wikipedia(), Fandom()])
def test_recorded_original_preview_caption_and_exact_evidence(
    source: Wikipedia | Fandom,
) -> None:
    records = discover(
        source,
        f'''<h2>Variants</h2><figure>
        <a class="image" href="{ORIGINAL}"><img src="{PREVIEW}"
        srcset="{LARGE} 2x" width="250"></a>
        <figcaption>Prototype radar on a test vehicle.</figcaption></figure>''',
    )
    by_url = {row.url: row for row in records}
    assert set(by_url) == {ORIGINAL, PREVIEW, LARGE}
    assert by_url[ORIGINAL].role == "original"
    assert by_url[LARGE].role == "preview"
    assert by_url[LARGE].original_url == ORIGINAL
    assert not by_url[LARGE].exclusion_reason
    assert by_url[PREVIEW].exclusion_reason == "redundant_preview"
    for row in records:
        assert row.caption == "Prototype radar on a test vehicle."
        assert row.section == "Variants"
        assert row.references[0].entity_id == f"{source.id}:1"
        assert row.references[0].evidence_id == "page"
        assert row.references[0].association == "source_context"
        assert not row.references[0].ambiguous


@pytest.mark.parametrize("source", [Wikipedia(), Fandom()])
def test_observed_preview_does_not_invent_an_original(
    source: Wikipedia | Fandom,
) -> None:
    records = discover(
        source,
        f'''<figure><a href="/wiki/File:Radar.jpg">
        <img src="{PREVIEW}" width="250" alt="Early radar prototype">
        </a></figure>''',
    )
    assert len(records) == 1
    assert records[0].url == PREVIEW
    assert records[0].role == "preview"
    assert records[0].original_url == PREVIEW
    assert records[0].caption == "Early radar prototype"


def test_unscaled_wikipedia_srcset_is_recorded_original() -> None:
    records = discover(
        Wikipedia(),
        f'''<img src="{PREVIEW}"
        srcset="{ORIGINAL} 2x" width="250">''',
    )
    assert {row.url: row.role for row in records} == {
        ORIGINAL: "original",
        PREVIEW: "preview",
    }
    assert (
        next(row for row in records if row.role == "preview").original_url == ORIGINAL
    )


def test_fandom_lazy_noscript_and_revision_urls() -> None:
    original = "https://static.wikia.nocookie.net/military/images/a/ab/Radar.jpg/revision/latest?cb=1"
    preview = original.replace("latest?", "latest/scale-to-width-down/250?")
    html = f'''<figure><a href="{original}"><img src="data:image/gif;base64,AAAA"
        data-src="{preview}" width="250" alt="Radar"></a>
        <noscript><a href="{original}"><img src="{preview}" width="250" alt="Radar"></a></noscript>
        <figcaption>Prototype</figcaption></figure>'''
    records = discover(Fandom(), html)
    assert len(records) == 2
    assert {row.url for row in records} == {original, preview}
    assert (
        next(row for row in records if row.role == "preview").original_url == original
    )


@pytest.mark.parametrize(
    "html",
    [
        f'<div class="navbox"><img src="{PREVIEW}"></div>',
        f'<div class="ambox"><img src="{PREVIEW}"></div>',
        f'<img src="{PREVIEW}" alt="Flag of China">',
        f'<img src="{PREVIEW}" alt="Commons logo">',
        f'<img src="{PREVIEW}" width="40" height="40">',
        f'<img src="{PREVIEW}" data-image-name="Russia location map.svg">',
    ],
)
def test_flags_logos_navigation_and_generic_maps_are_explicitly_excluded(
    html: str,
) -> None:
    records = discover(Wikipedia(), html)
    assert records
    assert all(row.exclusion_reason == "layout_image" for row in records)


def test_unrelated_sections_are_excluded_but_later_subject_sections_are_retained() -> (
    None
):
    records = discover(
        Wikipedia(),
        f'''<h2>See also</h2><img src="{PREVIEW}">
        <h2>Development</h2><img src="{LARGE}">''',
    )
    assert (
        next(row for row in records if row.url == PREVIEW).exclusion_reason
        == "unrelated_section"
    )
    assert not next(row for row in records if row.url == LARGE).exclusion_reason


def test_headings_ignore_external_layout_and_edit_controls() -> None:
    records = Wikipedia().discover_media(
        WIKI,
        f'''<h2>Contents</h2><div class="mw-parser-output"><img src="{PREVIEW}">
        <h2>References<span class="mw-editsection">[edit]</span></h2>
        <h3>Books</h3><img src="{LARGE}"></div>'''.encode(),
        [{"id": "wikipedia:1", "evidence": [{"id": "page", "url": WIKI}]}],
    )
    assert next(row for row in records if row.url == PREVIEW).section == "Lead"
    assert (
        next(row for row in records if row.url == LARGE).exclusion_reason
        == "unrelated_section"
    )


def test_infobox_and_gallery_captions_take_precedence_over_filename_alt() -> None:
    records = discover(
        Fandom(),
        f'''<table class="infobox"><tr><td>
        <img src="{PREVIEW}" alt="file"><div class="infobox-caption">Antenna at an exhibition</div>
        </td></tr></table><ul><li class="gallerybox"><div class="thumb">
        <img src="{LARGE}" alt="file"></div><div class="gallerytext">Prototype side array</div></li></ul>''',
    )
    assert (
        next(row for row in records if row.url == PREVIEW).caption
        == "Antenna at an exhibition"
    )
    assert next(row for row in records if row.url == PREVIEW).section == "Infobox"
    assert (
        next(row for row in records if row.url == LARGE).caption
        == "Prototype side array"
    )


def test_only_matching_evidence_is_referenced_and_shared_pages_are_ambiguous() -> None:
    owners = [
        {
            "id": "wikipedia:1",
            "evidence": [
                {"id": "right", "url": WIKI},
                {"id": "wrong", "url": WIKI + "-other"},
            ],
        },
        {"id": "wikipedia:2", "evidence": [{"id": "second", "url": WIKI}]},
        {"id": "wikipedia:3", "evidence": [{"id": "absent", "url": WIKI + "-other"}]},
    ]
    records = discover(Wikipedia(), f'<img src="{PREVIEW}">', entities=owners)
    assert {(ref.entity_id, ref.evidence_id) for ref in records[0].references} == {
        ("wikipedia:1", "right"),
        ("wikipedia:2", "second"),
    }
    assert all(ref.ambiguous for ref in records[0].references)
    assert discover(Wikipedia(), f'<img src="{PREVIEW}">', entities=[]) == []


def test_unsupported_origins_formats_and_declared_oversized_originals() -> None:
    records = discover(
        Wikipedia(),
        f'''<img src="https://outside.test/photo.jpg">
        <img src="https://upload.wikimedia.org/wikipedia/commons/a/ab/anim.gif">
        <a href="{ORIGINAL}"><img src="{PREVIEW}" data-file-width="10000" data-file-height="10000"></a>''',
    )
    by_url = {row.url: row for row in records}
    assert (
        by_url["https://outside.test/photo.jpg"].exclusion_reason
        == "unsupported_media_origin"
    )
    assert (
        by_url[
            "https://upload.wikimedia.org/wikipedia/commons/a/ab/anim.gif"
        ].exclusion_reason
        == "unsupported_image_format"
    )
    assert by_url[ORIGINAL].exclusion_reason == "declared_image_too_many_pixels"
    assert not by_url[PREVIEW].exclusion_reason


@pytest.mark.parametrize("source", [Wikipedia(), Fandom()])
def test_no_images_is_empty_and_media_protocol_is_shared(
    source: Wikipedia | Fandom,
) -> None:
    assert isinstance(source, MediaSource)
    assert discover(source, "<p>No image is supplied.</p>") == []
    assert discover(source, '<span class="mw-broken-media">Radar.jpg</span>') == []
    assert discover(source, '<img src="data:image/gif;base64,AAAA">') == []


def test_missing_article_containers_fail_explicitly() -> None:
    owners = [{"id": "wikipedia:1", "evidence": [{"id": "page", "url": WIKI}]}]
    with pytest.raises(ValueError, match="archived article body"):
        Wikipedia().discover_media(WIKI, b"<html></html>", owners)
    with pytest.raises(ValueError, match="archived rendered article"):
        Fandom().discover_media(FANDOM, b"{}", owners)
