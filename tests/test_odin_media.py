import json
from copy import deepcopy
from pathlib import Path

import pytest

from pipelines.media import MediaStore
from pipelines.sources.base import MediaSource
from pipelines.sources.odin import HOST, ORIGIN, Odin, page_url
from pipelines.sources.odin.media import media_url, record_images

URL = page_url(0)
ID_A = "1" * 32
ID_B = "2" * 32
ASSET = "a" * 32
IMAGE = f"/dA/{ASSET}/fileAsset/Vehicle%20(A)-ea38"
IMAGE_URL = ORIGIN + "/dotcms" + IMAGE


def record(identity: str = ID_A, **values: object) -> dict:
    return {
        "identifier": identity,
        "contentType": "WegCard",
        "live": True,
        "archived": False,
        "host": HOST,
        "name": "Example vehicle",
        "images": json.dumps([{"name": "Vehicle%20(A)", "url": IMAGE}]),
        "sections": "[]",
        **values,
    }


def encoded(*records: dict) -> bytes:
    return json.dumps(
        {
            "entity": {
                "resultsSize": len(records),
                "jsonObjectView": {"contentlets": list(records)},
            }
        }
    ).encode()


def entity(identity: str = ID_A, *, url: str = URL, **evidence: object) -> dict:
    return {
        "id": "odin:" + identity,
        "evidence": [
            {
                "id": "odin-evidence-" + identity,
                "url": url,
                "record_id": identity,
                **evidence,
            }
        ],
    }


def test_all_gallery_images_use_original_urls_and_record_level_associations() -> None:
    source = Odin()
    assert isinstance(source, MediaSource)
    legacy = "images/4/49/Vehicle rear (B)-dd1d"
    gallery = [
        {"name": "Vehicle%20(A)", "url": IMAGE},
        {"name": "Vehicle rear (B)", "url": legacy},
        {"name": "Family diagram", "url": IMAGE.replace("ea38", "diagram.png")},
    ]
    records = [record(images=json.dumps(gallery)), record(ID_B)]
    entities = [entity(), entity(ID_B), entity("3" * 32, url=page_url(100))]
    original = deepcopy(entities)
    result = list(source.discover_media(URL, encoded(*records), entities))
    assert entities == original
    assert len(result) == 4
    assert [image.url for image in result[:2]] == [
        IMAGE_URL,
        ORIGIN + "/dotcms/images/4/49/Vehicle%20rear%20(B)-dd1d",
    ]
    assert [image.caption for image in result[:3]] == [
        "Vehicle (A)",
        "Vehicle rear (B)",
        "Family diagram",
    ]
    for index, image in enumerate(result):
        identity = ID_A if index < 3 else ID_B
        assert len(image.references) == 1
        reference = image.references[0]
        assert reference.entity_id == "odin:" + identity
        assert reference.evidence_id == "odin-evidence-" + identity
        assert reference.caption == image.caption
        assert reference.association == "source_context"
        assert not reference.ambiguous
        assert image.page_url == URL
        assert image.role == "original"
        assert not image.original_url
        assert not image.exclusion_reason


def test_wrong_record_or_page_never_borrows_another_entity_owner() -> None:
    entities = [entity(record_id=ID_B), entity(ID_B), entity(url=page_url(100))]
    result = list(Odin().discover_media(URL, encoded(record()), entities))
    assert len(result) == 1
    assert not result[0].references


def test_embedded_images_keep_section_context_and_all_picture_sources() -> None:
    diagram = IMAGE.replace("ea38", "diagram.png")
    detail = IMAGE.replace("ea38", "detail.jpg")
    item = record(
        notes=f'<p>Example</p><img src="{IMAGE}" alt="Side view">',
        sections=json.dumps(
            [
                {
                    "name": "Dimensions",
                    "sections": [
                        {
                            "name": "Family diagram",
                            "properties": [
                                {
                                    "name": "Drawing",
                                    "value": (
                                        f'<picture><source srcset="{diagram} 1x, {detail} 2x">'
                                        f'<img data-src="{diagram}" alt="Layout"></picture>'
                                    ),
                                }
                            ],
                        }
                    ],
                },
            ]
        ),
    )
    result = list(Odin().discover_media(URL, encoded(item), [entity()]))
    assert len(result) == 5
    assert result[1].caption == "Side view"
    assert result[1].section == "Notes"
    assert [image.section for image in result[2:]] == [
        "Dimensions / Family diagram / Drawing"
    ] * 3
    assert result[-1].caption == "Layout"


def test_placeholders_icons_and_source_citations_are_not_pictures() -> None:
    item = record(
        images="[]",
        hasTitleImage=False,
        titleImage="TITLE_IMAGE_NOT_FOUND",
        __icon__="contentIcon",
        contentTypeIcon="event_note",
        sections=json.dumps(
            [
                {
                    "name": "Image Sources",
                    "properties": [
                        {"name": "Notes", "value": "https://example.org/photo.jpg"}
                    ],
                }
            ]
        ),
    )
    assert record_images(item) == []
    assert list(Odin().discover_media(URL, encoded(item), [entity()])) == []
    item.update(hasTitleImage=True, titleImage=IMAGE)
    result = list(Odin().discover_media(URL, encoded(item), [entity()]))
    assert len(result) == 1
    assert result[0].section == "Title image"


@pytest.mark.parametrize(
    "gallery", [False, 42, {}, "{", "{}", [None], [{}], [{"url": ""}]]
)
def test_malformed_gallery_is_never_silently_omitted(gallery: object) -> None:
    with pytest.raises(ValueError, match="ODIN"):
        record_images(record(images=gallery))


@pytest.mark.parametrize("gallery", [None, "", [], "[]"])
def test_source_without_images_is_explicitly_empty(gallery: object) -> None:
    assert record_images(record(images=gallery)) == []


@pytest.mark.parametrize(
    "value",
    [
        IMAGE_URL,
        ORIGIN + IMAGE,
        ORIGIN + "/dotcms/images/c/ce/Vehicle%20(C)-fa00",
        ORIGIN + f"/dotcms/dA/{ASSET}/fileAsset/V%C3%A9hicule%2520.jpg",
        ORIGIN + "/dotcms/dA/00000000-1111-2222-3333-444444444444/fileAsset/image.jpg",
    ],
)
def test_known_original_and_redirect_paths_are_allowed(value: str) -> None:
    assert media_url(value) == value
    headers = Odin().request_headers(value)
    assert headers["Accept"].startswith("image/")
    assert "Cookie" not in headers
    assert "Authorization" not in headers


@pytest.mark.parametrize(
    "value",
    [
        IMAGE_URL.replace("https:", "http:"),
        IMAGE_URL.replace("odin.t2com.army.mil", "other.example"),
        IMAGE_URL.replace("odin.t2com.army.mil", "user@odin.t2com.army.mil"),
        IMAGE_URL + "?token=example",
        IMAGE_URL + "#image",
        ORIGIN + "/dotcms/images/logo.png",
        ORIGIN + "/dotcms/private/image.jpg",
        IMAGE_URL.replace("Vehicle%20(A)-ea38", "../secret.jpg"),
        IMAGE_URL.replace("Vehicle%20(A)-ea38", "%2e%2e"),
        IMAGE_URL.replace("Vehicle%20(A)-ea38", "%252e%252e"),
        IMAGE_URL.replace("Vehicle%20(A)-ea38", "Vehicle%2fsecret.jpg"),
        IMAGE_URL.replace("Vehicle%20(A)-ea38", "Vehicle%255csecret.jpg"),
        IMAGE_URL + "%0a",
    ],
)
def test_media_path_allowlist_rejects_other_resources(value: str) -> None:
    assert media_url(value) is None
    with pytest.raises(ValueError, match="Outside"):
        Odin().request_headers(value)


def test_unsupported_external_and_navigation_images_are_accounted_for() -> None:
    gallery = [
        {"url": "https://other.example/photo.jpg"},
        {"url": "/assets/odin-logo.svg"},
        {"url": IMAGE + ".gif"},
        {"url": IMAGE},
    ]
    result = list(
        Odin().discover_media(URL, encoded(record(images=gallery)), [entity()])
    )
    assert [image.exclusion_reason for image in result] == [
        "outside_media_scope",
        "outside_media_scope",
        "unsupported_image_format",
        "",
    ]
    assert all(image.references for image in result)


def test_shared_store_deduplicates_bytes_url_but_retains_both_owners(
    tmp_path: Path,
) -> None:
    candidates = Odin().discover_media(
        URL, encoded(record(), record(ID_B)), [entity(), entity(ID_B)]
    )
    with MediaStore(tmp_path) as store:
        store.register("odin", "test", candidates)
        records = store.records("odin", "test")
        assert len(records) == 1
        assert records[0]["url"] == IMAGE_URL
        assert records[0]["state"] == "pending"
        assert {ref["entity_id"] for ref in records[0]["references"]} == {
            "odin:" + ID_A,
            "odin:" + ID_B,
        }
