from pipelines.sources.base import MediaSource
from pipelines.sources.virtualglobetrotting import VirtualGlobetrotting

URL = "https://virtualglobetrotting.com/map/example/"
LARGE = "https://c1.vgtstatic.com/thumb/1/2/12345-v1-xl/example.jpg"
SMALL = LARGE.replace("-xl/", "-l/")
BING = SMALL.replace("c1.", "c2.").replace("/thumb/", "/thumbll/")


def test_map_id_and_provider_grouping_use_only_recorded_urls() -> None:
    body = f'''<meta property="og:image" content="{LARGE}">
    <div class="map-info-coordinates"><a href="/map/12345/nearby/">Place</a></div>
    <div class="map-info-thumbs-l"><img src="{SMALL}" alt="Example (Google Maps)">
    <img src="{BING}" alt="Example (Bing Maps)"></div>
    <aside><img class="map-thumb" src="https://c1.vgtstatic.com/thumb/1/2/99999-v1-l/other.jpg"></aside>
    <img src="https://o.vgtstatic.com/images/t.gif">'''.encode()
    source = VirtualGlobetrotting()
    assert isinstance(source, MediaSource)
    owners = [
        {"id": "virtualglobetrotting:12345", "evidence": [{"id": "e1", "url": URL}]},
        {"id": "virtualglobetrotting:other", "evidence": [{"id": "e2", "url": URL}]},
    ]
    rows = list(source.discover_media(URL, body, owners))
    eligible = [row for row in rows if not row.exclusion_reason]
    assert [row.url for row in eligible] == [LARGE, SMALL, BING]
    assert [row.original_url for row in eligible] == [LARGE, LARGE, BING]
    assert all(row.role == "preview" for row in eligible)
    assert eligible[0].caption == "Example (Google Maps)"
    assert all(
        [ref.entity_id for ref in row.references] == ["virtualglobetrotting:12345"]
        for row in eligible
    )
    assert {row.exclusion_reason for row in rows if row.exclusion_reason} == {
        "unrelated_image",
        "placeholder_image",
    }
    assert not any(row.references for row in rows if row.exclusion_reason)


def test_metadata_for_wrong_map_and_unapproved_host_are_excluded() -> None:
    for value, reason in [
        (LARGE.replace("12345-", "99999-"), "unrelated_image"),
        (
            LARGE.replace("c1.vgtstatic.com", "other.example"),
            "unsupported_media_origin",
        ),
    ]:
        body = f'<meta property="og:image" content="{value}"><div class="map-info-coordinates"><a href="/map/12345/nearby/">Place</a></div>'.encode()
        rows = list(VirtualGlobetrotting().discover_media(URL, body, []))
        assert rows[0].exclusion_reason == reason
