from pipelines.sources.base import MediaSource
from pipelines.sources.cambridgepixel import HEADERS, SEED, CambridgePixel, identity


def test_catalog_icons_are_excluded_without_attaching_them_to_radar_records() -> None:
    source = CambridgePixel()
    assert isinstance(source, MediaSource)
    body = b"""<main><img src="/site/dist/assets/images/icons/radar-types/air_defence.jpg">
    <img src="/site/dist/assets/images/logo/logo.png"><footer><img src="/footer.jpg"></footer></main>"""
    entities = [{"id": "cambridgepixel:one", "evidence": [{"id": "e1", "url": SEED}]}]
    result = list(source.discover_media(SEED, body, entities))
    assert [row.exclusion_reason for row in result] == [
        "category_icon",
        "site_logo",
        "navigation_image",
    ]
    assert not any(row.references for row in result)


def test_actual_row_photo_uses_manufacturer_model_identity_not_shared_page() -> None:
    headers = "".join(f"<th>{value}</th>" for value in HEADERS)
    body = f"""<table><thead>{headers}</thead><tbody><tr><td>Acme</td><td>Radar A</td>
    <td>X</td><td>Current</td><td><img src="/media/radar.jpg" alt="Side view"></td><td></td>
    </tr></tbody></table>""".encode()
    owner = "cambridgepixel:" + identity("Acme", "Radar A")
    entities = [
        {"id": key, "evidence": [{"id": key + "-e", "url": SEED}]}
        for key in [owner, "cambridgepixel:other"]
    ]
    result = list(CambridgePixel().discover_media(SEED, body, entities))
    assert len(result) == 1 and not result[0].exclusion_reason
    assert [ref.entity_id for ref in result[0].references] == [owner]
    assert result[0].caption == "Side view"
