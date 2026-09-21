import json
from copy import deepcopy

import pytest

from pipelines.sources.base import MediaSource
from pipelines.sources.militaryperiscope import (
    ORIGIN,
    TOC,
    TRIAL,
    MilitaryPeriscope,
    page_url,
)

URL = page_url("/weapons/radars/example/overview/")
IMAGE = "/wt/media/original_images/radar%20side.jpg"


def entity(identity: str = "militaryperiscope:100", url: str = URL) -> dict:
    return {"id": identity, "evidence": [{"id": "evidence-100", "url": url}]}


def encoded(props: dict) -> bytes:
    return json.dumps(
        {"component_name": "WeaponDetailPage", "component_props": props}
    ).encode()


def test_structured_gallery_captions_sections_and_page_associations() -> None:
    source = MilitaryPeriscope()
    assert isinstance(source, MediaSource)
    props = {
        "title": "Overview",
        "readable_name": {"variants": "Variants and versions"},
        "section": [
            {
                "type": "variants",
                "value": [
                    {
                        "type": "section",
                        "value": {
                            "header": "Radar A",
                            "subheader": "Side views",
                            "body": [
                                {
                                    "type": "images",
                                    "value": [
                                        {
                                            "image": {
                                                "path": IMAGE,
                                                "title": "Radar photograph",
                                                "caption": "Vehicle in transport configuration",
                                                "source": "Publisher credit",
                                            }
                                        }
                                    ],
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
    entities = [entity(), entity("militaryperiscope:unrelated", TOC)]
    original = deepcopy(entities)
    result = list(source.discover_media(URL, encoded(props), entities))
    assert entities == original
    assert len(result) == 1
    image = result[0]
    assert image.url == ORIGIN + IMAGE
    assert image.page_url == URL
    assert image.role == "original"
    assert image.original_url == ""
    assert (
        image.caption
        == "Radar photograph\nVehicle in transport configuration\nSource: Publisher credit"
    )
    assert (
        image.section
        == "Overview / Variants and versions / Radar A / Side views / Images"
    )
    assert [(ref.entity_id, ref.evidence_id) for ref in image.references] == [
        ("militaryperiscope:100", "evidence-100")
    ]
    assert image.references[0].caption == image.caption
    assert image.references[0].section == image.section
    assert image.references[0].ambiguous is False


def test_unassociated_collection_photos_and_navigation_are_accounted_for() -> None:
    source = MilitaryPeriscope()
    props = {
        "title": "Aircraft",
        "photo": {"path": IMAGE, "title": "Aircraft illustration"},
        "items": [
            {
                "title": "Aerostats",
                "photo": {"path": IMAGE, "title": "Aerostat illustration"},
            }
        ],
        "regions": [
            {
                "title": "Africa",
                "photo": {
                    "path": "/wt/media/original_images/Africa.png",
                    "title": "Africa map",
                },
            }
        ],
    }
    collection_url = page_url("/weapons/aircraft/")
    result = list(source.discover_media(collection_url, encoded(props), []))
    assert len(result) == 3
    assert all(not image.references and not image.exclusion_reason for image in result)
    assert result[1].section == "Aircraft / Aerostats"
    assert result[2].caption == "Africa map"
    assert all(image.page_url == collection_url for image in result)
    logos = list(
        source.discover_media(
            TRIAL,
            b'<nav><img src="/img/mp-logo.svg" alt="Military Periscope Logo"></nav>',
            [],
        )
    )
    assert len(logos) == 1
    assert logos[0].exclusion_reason == "navigation_image"
    assert logos[0].caption == "Military Periscope Logo"
    assert logos[0].references == []


def test_inline_images_multiple_contexts_and_ambiguous_owners() -> None:
    source = MilitaryPeriscope()
    props = {
        "title": "Overview",
        "content": [
            {
                "type": "paragraph",
                "value": f'<p>Radar diagram</p><img src="{IMAGE}" alt="Antenna arrangement">',
            },
            {
                "type": "styled_block",
                "value": {
                    "header": "Variant A",
                    "body": [
                        {
                            "type": "image",
                            "value": {"path": IMAGE, "caption": "Front view"},
                        }
                    ],
                },
            },
        ],
    }
    result = list(
        source.discover_media(
            URL, encoded(props), [entity(), entity("militaryperiscope:101")]
        )
    )
    assert len(result) == 2
    assert result[0].url == result[1].url
    assert result[0].caption == "Antenna arrangement"
    assert result[1].section == "Overview / Variant A"
    assert all(len(image.references) == 2 for image in result)
    assert all(
        reference.ambiguous for image in result for reference in image.references
    )


def test_public_media_headers_never_read_page_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = MilitaryPeriscope()
    monkeypatch.delenv("MILITARYPERISCOPE_COOKIE", raising=False)
    monkeypatch.setenv("MILITARYPERISCOPE_COOKIE_FILE", "missing-cookie-file")
    headers = source.request_headers(ORIGIN + IMAGE)
    assert headers == {"Accept": "image/jpeg,image/png,image/webp"}
    assert source.media_origins == (ORIGIN,)
    assert source.media_request_interval == 0.2
    monkeypatch.setenv("MILITARYPERISCOPE_COOKIE", "sessionid=fixture-only")
    assert source.request_headers(URL)["Cookie"] == "sessionid=fixture-only"
    assert source.request_headers(ORIGIN + IMAGE) == headers
    for url in (
        "https://other.example" + IMAGE,
        ORIGIN + "/wt/media/original_images/../private.jpg",
        ORIGIN + "/wt/media/original_images/%2e%2e/private.jpg",
        ORIGIN + "/wt/media/original_images/%252e%252e/private.jpg",
        ORIGIN + "/wt/media/original_images/photo.jpg?session=secret",
        ORIGIN + "/accounts/private.jpg",
        ORIGIN + "/img/mp-logo.svg",
    ):
        with pytest.raises(ValueError, match="outside"):
            source.request_headers(url)


def test_unsupported_and_outside_images_are_explicitly_excluded() -> None:
    source = MilitaryPeriscope()
    props = {
        "section": [
            {"type": "image", "value": {"path": "https://other.example/photo.jpg"}},
            {
                "type": "image",
                "value": {"path": "/wt/media/original_images/animated.gif"},
            },
        ]
    }
    result = list(source.discover_media(URL, encoded(props), [entity()]))
    assert [image.exclusion_reason for image in result] == [
        "outside_media_scope",
        "unsupported_image_format",
    ]
    assert all(image.references for image in result)
    assert list(source.discover_media(TOC, b'{"sections": []}', [])) == []
    with pytest.raises(ValueError, match="Outside"):
        list(source.discover_media("https://other.example/page", encoded(props), []))


@pytest.mark.parametrize(
    "block",
    [{"type": "image", "value": {}}, {"type": "images", "value": "not-a-gallery"}],
)
def test_malformed_media_is_not_silently_omitted(block: dict) -> None:
    with pytest.raises(ValueError, match="Military Periscope"):
        list(MilitaryPeriscope().discover_media(URL, encoded({"section": [block]}), []))
