from copy import deepcopy

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
