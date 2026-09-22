import hashlib
from copy import deepcopy
from io import BytesIO

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
)
from test_cambridgepixel import page, product
from test_cambridgepixel_imagery import MATCH, URL

from pipelines.sources.cambridgepixel import SEED, CambridgePixel
from pipelines.sources.cambridgepixel.imagery import discover, extract, validate_page
from pipelines.sources.cambridgepixel.pdf_image import extract as extract_image


@pytest.fixture
def reviewed_pdf() -> tuple[bytes, dict]:
    writer = PdfWriter()
    sheet = writer.add_blank_page(width=100, height=100)
    image = DecodedStreamObject()
    image.set_data(bytes([80, 140, 200]) * 12 * 8)
    image.update(
        {
            NameObject("/Type"): NameObject("/XObject"),
            NameObject("/Subtype"): NameObject("/Image"),
            NameObject("/Width"): NumberObject(12),
            NameObject("/Height"): NumberObject(8),
            NameObject("/ColorSpace"): NameObject("/DeviceRGB"),
            NameObject("/BitsPerComponent"): NumberObject(8),
        }
    )
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    sheet[NameObject("/Resources")] = DictionaryObject(
        {
            NameObject("/XObject"): DictionaryObject(
                {NameObject("/Radar"): writer._add_object(image)}
            ),
            NameObject("/Font"): DictionaryObject({NameObject("/F1"): font}),
        }
    )
    content = DecodedStreamObject()
    content.set_data(
        f"q 12 0 0 8 0 0 cm /Radar Do Q BT /F1 10 Tf 0 50 Td ({MATCH['quote']}) Tj ET".encode()
    )
    sheet[NameObject("/Contents")] = writer._add_object(content)
    output = BytesIO()
    writer.write(output)
    body = output.getvalue()
    raster = PdfReader(BytesIO(body)).pages[0].images[0]
    review = {
        **MATCH,
        "image_url": URL,
        "pdf_image": {
            "page": 1,
            "name": raster.name,
            "document_sha256": hashlib.sha256(body).hexdigest(),
            "image_sha256": hashlib.sha256(raster.data).hexdigest(),
            "extractor": "pypdf-6.10.0",
        },
    }
    return body, review


def test_reviewed_pdf_preserves_image_document_and_entity_evidence(
    reviewed_pdf: tuple[bytes, dict],
) -> None:
    body, review = reviewed_pdf
    raster, mime = extract_image(body, review)
    assert mime == "image/png"
    source = CambridgePixel()
    source.prepare([(SEED, page([product()]))])
    entity = extract(URL, body, [review], source.catalog_entities)[0]
    assert "data:image/png;base64," in entity.evidence[0].rendered_html
    assert "![" not in entity.evidence[0].markdown
    candidate = discover(URL, body, [entity.metadata(source.id)], [review])[0]
    assert candidate.embedded_body == raster
    assert candidate.role == "preview"
    assert candidate.original_url == candidate.page_url == candidate.url == URL
    assert candidate.references[0].entity_id == entity.metadata(source.id)["id"]


@pytest.mark.parametrize(
    "field,value,message",
    [
        ("document_sha256", "0" * 64, "PDF changed"),
        ("image_sha256", "0" * 64, "image changed"),
        ("name", "Missing.png", "image is missing"),
        ("page", 2, "page is missing"),
        ("page", True, "page is missing"),
    ],
)
def test_reviewed_pdf_rejects_changed_selection(
    reviewed_pdf: tuple[bytes, dict], field: str, value: object, message: str
) -> None:
    body, review = reviewed_pdf
    review["pdf_image"][field] = value
    with pytest.raises(ValueError, match=message):
        extract_image(body, review)


def test_reviewed_pdf_requires_exact_quote_and_document_url(
    reviewed_pdf: tuple[bytes, dict],
) -> None:
    body, review = reviewed_pdf
    for changed, message in [
        ({**review, "quote": "Another model"}, "quote changed"),
        ({**review, "quote": ""}, "quote changed"),
        ({**review, "image_url": "https://other.test/file.pdf"}, "PDF changed"),
    ]:
        with pytest.raises(ValueError, match=message):
            extract_image(body, changed)
    html_review = deepcopy(review)
    del html_review["pdf_image"]
    with pytest.raises(ValueError, match="Mixed PDF and HTML"):
        validate_page(URL, body, [review, html_review])
