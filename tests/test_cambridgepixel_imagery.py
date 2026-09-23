from copy import deepcopy
from urllib.parse import quote

import pytest
from test_cambridgepixel import page, product

from pipelines.sources.cambridgepixel import SEED, CambridgePixel
from pipelines.sources.cambridgepixel.imagery import discover, extract, validate_page

URL = "https://manufacturer.test/radar/"
IMAGE = "https://cdn.manufacturer.test/radar.jpg"
MATCH = {
    "manufacturer": "Example",
    "model": "Watchman",
    "page_url": URL,
    "image_url": IMAGE,
    "quote": "Watchman is a surveillance radar.",
    "caption": "Example Watchman radar",
    "search_url": "https://www.google.com/search?udm=2&q=Example+Watchman",
    "notes": "Model-labelled product photograph.",
    "status": "matched",
}
BODY = f'<h1>Example Watchman</h1><p>{MATCH["quote"]}</p><img src="{IMAGE}">'.encode()


def test_reviewed_photo_requires_exact_captured_model_and_image_evidence() -> None:
    source = CambridgePixel()
    source.prepare([(SEED, page([product()]))])
    entities = extract(URL, BODY, [MATCH], source.catalog_entities)
    assert len(entities) == 1
    base = next(iter(source.catalog_entities.values()))
    base.merge(entities[0])
    metadata = base.metadata(source.id)
    candidates = discover(URL, BODY, [metadata], [MATCH])
    assert len(candidates) == 1 and candidates[0].url == IMAGE
    ref = candidates[0].references[0]
    assert ref.entity_id == metadata["id"]
    assert ref.evidence_id == entities[0].evidence[0].id
    assert ref.association == "reviewed_model_match"
    assert candidates[0].page_url == URL
    assert entities[0].evidence[0].records == [MATCH]
    with pytest.raises(ValueError, match="no captured"):
        discover(URL, BODY, [], [MATCH])
    with pytest.raises(ValueError, match="evidence changed"):
        validate_page(URL, BODY.replace(b"Watchman is", b"Other model is"), [MATCH])
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, BODY.replace(b"radar.jpg", b"other.jpg"), [MATCH])
    wrong = deepcopy(MATCH)
    wrong["page_url"] = "https://other.test/"
    assert not discover(URL, BODY, [metadata], [wrong])


@pytest.mark.parametrize(
    "markup",
    [
        f'<video poster="{IMAGE}"></video>',
        f'<img src="/blank.png" data-src-url-d="{IMAGE}">',
        f'<div style="background-image: url({IMAGE})"></div>',
        f'<div style="background: url({IMAGE}) 50% center / cover no-repeat"></div>',
        f"<div style=\"color:red; background-image: url('{IMAGE}')\"></div>",
        f'<div role="img" data-thumbnail="{IMAGE}"></div>',
        f'<picture><source srcset="{IMAGE}"><img src="/fallback.jpg"></picture>',
        f'<a href="{IMAGE}"><img src="/thumbnail.jpg"></a>',
        f'<a href="{IMAGE}" type="image/jpeg; length=2521101">Download</a>',
        f'<img src="/thumbnail.jpg"><a href="{IMAGE}" title="Watchman (click to enlarge)"></a>',
        f'<a href="{IMAGE}"><div role="img" data-thumbnail="/thumbnail.jpg"></div></a>',
        f'<picture><source srcset="/small.jpg 800w, {IMAGE} 1600w"><img src="/fallback.jpg"></picture>',
        f'<img src="/_next/image?url={quote(IMAGE, safe="")}&amp;w=1920">',
    ],
)
def test_reviewed_original_can_be_a_poster_link_or_image_proxy(markup: str) -> None:
    body = f"<p>{MATCH['quote']}</p>{markup}".encode()
    validate_page(URL, body, [MATCH])
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body.replace(b"radar.jpg", b"other.jpg"), [MATCH])


def test_family_illustration_keeps_ambiguous_association() -> None:
    source = CambridgePixel()
    source.prepare([(SEED, page([product()]))])
    match = {**MATCH, "ambiguous": True}
    entity = extract(URL, BODY, [match], source.catalog_entities)[0]
    candidate = discover(URL, BODY, [entity.metadata(source.id)], [match])[0]
    assert candidate.references[0].ambiguous
    assert candidate.references[0].association == "reviewed_family_context"


def test_plain_link_is_not_image_evidence() -> None:
    body = f'<p>{MATCH["quote"]}</p><a href="{IMAGE}">Related page</a>'.encode()
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body, [MATCH])


@pytest.mark.parametrize("property_name", ["--background-image", "content"])
def test_non_image_css_reference_is_not_image_evidence(property_name: str) -> None:
    body = (
        f'<p>{MATCH["quote"]}</p><div style="{property_name}: url({IMAGE})"></div>'
    ).encode()
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body, [MATCH])


def test_image_url_escaping_preserves_reserved_path_characters() -> None:
    match = {**MATCH, "image_url": "https://cdn.manufacturer.test/radar%20photo.jpg"}
    body = BODY.replace(
        IMAGE.encode(), b"https://cdn.manufacturer.test/radar photo.jpg"
    )
    validate_page(URL, body, [match])
    match["image_url"] = "https://cdn.manufacturer.test/radar%2Fphoto.jpg"
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body.replace(b"radar photo", b"radar/photo"), [match])


def test_custom_image_css_property_requires_explicit_review() -> None:
    body = (
        f'<p>{MATCH["quote"]}</p><div style="--card-image:url({IMAGE})"></div>'.encode()
    )
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body, [MATCH])
    match = {**MATCH, "image_css_property": "--card-image"}
    validate_page(URL, body, [match])
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body.replace(b"--card-image", b"--other-image"), [match])
    with pytest.raises(ValueError, match="Invalid reviewed image CSS property"):
        validate_page(URL, body, [{**match, "image_css_property": ".*"}])


def test_relative_image_paths_preserve_empty_segments() -> None:
    url = "https://manufacturer.test/en/products/model"
    match = {
        **MATCH,
        "page_url": url,
        "image_url": "https://manufacturer.test/images//radar.jpg?size=original",
    }
    body = BODY.replace(IMAGE.encode(), b"../../images//radar.jpg?size=original")
    validate_page(url, body, [match])
    with pytest.raises(ValueError, match="image changed"):
        validate_page(url, body.replace(b"images//", b"images/"), [match])


@pytest.mark.parametrize("base", ["/", "https://manufacturer.test/"])
def test_image_paths_follow_document_base(base: str) -> None:
    match = {**MATCH, "image_url": "https://manufacturer.test/images/radar.jpg"}
    body = (
        f'<base href="{base}"><base href="https://ignored.test/">'
        f'<p>{MATCH["quote"]}</p><img src="images/radar.jpg">'
    ).encode()
    validate_page(URL, body, [match])
    with pytest.raises(ValueError, match="image changed"):
        validate_page(URL, body.replace(base.encode(), b"/other/", 1), [match])
