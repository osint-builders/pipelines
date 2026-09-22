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


@pytest.mark.parametrize("markup", [
    f'<video poster="{IMAGE}"></video>',
    f'<a href="{IMAGE}"><img src="/thumbnail.jpg"></a>',
    f'<img src="/_next/image?url={quote(IMAGE, safe="")}&amp;w=1920">',
])
def test_reviewed_original_can_be_a_poster_link_or_image_proxy(markup: str) -> None:
    body = f'<p>{MATCH["quote"]}</p>{markup}'.encode()
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
