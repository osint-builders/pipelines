import json

from pipelines.sources.base import MediaSource
from pipelines.sources.climateviewer import DATA, MAP, ClimateViewer, marker_key


def test_point_images_bind_record_ids_and_keep_site_depiction_uncertain() -> None:
    photo = "http://i47.tinypic.com/23rr760.jpg"
    features = [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [index, 50]},
            "properties": {
                "name": f"Site-{index}",
                "description": f'<br>S-300 site {index}<br><img src="{photo}"><img src="http://climateviewer.org/img/icons/flags/Flag-Big-USSR.jpg"><img src="http://img651.imageshack.us/img651/8043/nopicm.gif">',
            },
        }
        for index in range(2)
    ]
    owners = [
        {
            "id": "climateviewer:" + marker_key(feature),
            "evidence": [
                {"url": DATA, "id": f"e{index}", "record_id": marker_key(feature)}
            ],
        }
        for index, feature in enumerate(features)
    ]
    source = ClimateViewer()
    assert isinstance(source, MediaSource)
    result = list(
        source.discover_media(
            DATA,
            json.dumps({"type": "FeatureCollection", "features": features}).encode(),
            owners,
        )
    )
    photos = [item for item in result if not item.exclusion_reason]
    assert len(photos) == 2
    assert [row.references[0].entity_id for row in photos] == [
        row["id"] for row in owners
    ]
    assert [row.references[0].evidence_id for row in photos] == ["e0", "e1"]
    assert all(row.references[0].ambiguous for row in photos)
    assert all(row.references[0].association == "source_context" for row in photos)
    assert all("site depiction unverified" in row.section for row in photos)
    assert {row.exclusion_reason for row in result if row not in photos} == {
        "country_flag",
        "placeholder_image",
    }
    assert not any(row.references for row in result if row.exclusion_reason)
    wrong = [
        {
            "id": owners[0]["id"],
            "evidence": [{"url": DATA, "id": "wrong", "record_id": "another-record"}],
        }
    ]
    assert not any(
        row.references
        for row in source.discover_media(
            DATA,
            json.dumps({"type": "FeatureCollection", "features": features}).encode(),
            wrong,
        )
    )


def test_map_gallery_is_not_assigned_to_every_point() -> None:
    result = list(
        ClimateViewer().discover_media(
            MAP,
            b'<div class="post-content"><img src="https://lh4.googleusercontent.com/proxy/example"></div><img src="/logo.svg">',
            [],
        )
    )
    assert [row.exclusion_reason for row in result] == [
        "collection_image",
        "navigation_image",
    ]
    assert not any(row.references for row in result)
