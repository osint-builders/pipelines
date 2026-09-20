import json
import socket
from copy import deepcopy
from pathlib import Path

import pytest

from pipelines.archive import Archive
from pipelines.build import publish
from pipelines.distribution import collect, collect_artifacts, content_digest
from pipelines.sources.climateviewer import (
    DATA,
    LICENSE,
    MAP,
    ClimateViewer,
    features,
    marker_key,
)

MAP_BODY = f'''<div class="post-content"><p>Integrated Air Defence of Russia 2010</p><a href="{DATA}">{DATA}</a><p>Map: Fortress Russia by Jim Lee is licensed under <a rel="license" href="{LICENSE}">CC BY-NC-SA 4.0</a>.</p></div>'''.encode()


def point(
    name: str = "Site_A",
    longitude: float = 25.0,
    description: str = "Example Radar Site</br>Location approx.<br>Credit: Example contributor<br>TAR: unknown",
) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [longitude, 50.0, 0]},
        "properties": {
            "name": name,
            "description": description,
            "styleHash": "original",
        },
    }


def overlay() -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": [[24, 49, 0], [25, 50, 0]]},
        "properties": {"name": "Illustrative overlay"},
    }


def collection(*items: dict) -> bytes:
    return json.dumps(
        {"type": "FeatureCollection", "features": list(items or (point(), overlay()))}
    ).encode()


def source(body: bytes | None = None) -> ClimateViewer:
    adapter = ClimateViewer()
    adapter.minimum_entities = 1
    adapter.prepare([(DATA, body or collection()), (MAP, MAP_BODY)])
    return adapter


def test_scope_is_two_explicit_resources_and_has_no_link_expansion() -> None:
    adapter = source()
    assert adapter.normalize(DATA) == DATA
    assert adapter.normalize(MAP + "#description") == MAP
    assert adapter.discover(DATA, collection()) == []
    assert adapter.discover(MAP, MAP_BODY) == []
    for url in [
        DATA + "?download=1",
        DATA.replace("climateviewer.org", "other.test"),
        DATA.replace("https://", "https://user@"),
        DATA.replace("2018", "2019"),
        MAP + "other/",
        DATA.replace("Fortress-Russia", "Other"),
    ]:
        assert adapter.normalize(url) is None


def test_point_identity_is_independent_of_order_and_duplicate_names_are_distinct() -> (
    None
):
    items = [point(), point(longitude=26), point("Other", 25), overlay()]
    adapter = source(collection(*items))
    entities = adapter.extract(DATA, collection(*items), [])
    reversed_entities = adapter.extract(DATA, collection(*reversed(items)), [])
    assert len(entities) == len({e.key for e in entities}) == 3
    assert sorted(
        [e.metadata(adapter.id) for e in entities], key=lambda e: e["id"]
    ) == sorted(
        [e.metadata(adapter.id) for e in reversed_entities], key=lambda e: e["id"]
    )
    changed = deepcopy(items[0])
    changed["properties"]["description"] += " Updated description."
    assert marker_key(changed) == marker_key(items[0])
    changed["geometry"]["coordinates"][0] = 26
    assert marker_key(changed) != marker_key(items[0])
    with pytest.raises(ValueError, match="Duplicate"):
        adapter.extract(DATA, collection(point(), point()), [])


def test_full_feature_and_uncertainty_survive_without_turning_components_into_entities() -> (
    None
):
    item = point(
        "S-300_1",
        description='S-300PT (S-300PT)</br>TER: 1 x FLAP LID<br>Site History: N/A<br>Location approx.<br><img src="http://images.test/original.jpg"><script>tracking()</script>',
    )
    entity = source().extract(DATA, collection(item, overlay()), [])[0]
    evidence = entity.evidence[0]
    assert entity.kind == "site" and entity.categories[-1] == "SAM sites"
    assert entity.aliases == ["S-300_1"]
    assert evidence.records == [item]
    assert evidence.record_id == entity.key
    assert "Location approx." in evidence.search_text
    assert "2010" in evidence.markdown and "2018" in evidence.markdown
    assert "N/A" in evidence.markdown and "original.jpg" in evidence.markdown
    assert (
        "styleHash" not in evidence.search_text
        and "tracking()" not in evidence.search_text
    )
    assert not any(
        f.name in {"Country", "Current status", "Range"} for f in entity.facts
    )
    assert any(
        f.name == "TER" and f.raw == "1 x FLAP LID" and f.values == []
        for f in entity.facts
    )
    assert "Attribution" not in entity.aliases
    assert LICENSE in evidence.attribution and "Jim Lee" in evidence.attribution


@pytest.mark.parametrize(
    ("desc", "category"),
    [
        ("MiG-31 Foxhound Former base", "Air bases"),
        ("A-135 Gazelle ABM launch Site", "ABM sites"),
        ("Unknown radar site", "Radar sites"),
    ],
)
def test_site_types_remain_sites(desc: str, category: str) -> None:
    entity = source().extract(DATA, collection(point(description=desc)), [])[0]
    assert entity.kind == "site" and entity.categories[-1] == category


@pytest.mark.parametrize(
    "change",
    [
        "nan",
        "latitude",
        "boolean",
        "missing-description",
        "no-name",
        "polygon",
        "short-line",
        "null-geometry",
    ],
)
def test_invalid_or_unreviewed_features_fail_closed(change: str) -> None:
    item = point()
    if change == "nan":
        item["geometry"]["coordinates"][0] = float("nan")
    elif change == "latitude":
        item["geometry"]["coordinates"][1] = 91
    elif change == "boolean":
        item["geometry"]["coordinates"][0] = True
    elif change == "missing-description":
        item["properties"].pop("description")
    elif change == "no-name":
        item["properties"]["name"] = ""
    elif change == "polygon":
        item["geometry"]["type"] = "Polygon"
    elif change == "null-geometry":
        item["geometry"] = None
    else:
        item = overlay()
        item["geometry"]["coordinates"] = []
    with pytest.raises(ValueError):
        features(collection(item))


def test_provenance_requires_matching_layer_and_license() -> None:
    for body in [
        b"<p>Challenge</p>",
        MAP_BODY.replace(b"by-nc-sa", b"by"),
        MAP_BODY.replace(b"2010", b"2026"),
        MAP_BODY.replace(b"Jim Lee", b"Unknown"),
        MAP_BODY.replace(DATA.encode(), b"other.json"),
    ]:
        with pytest.raises(ValueError):
            ClimateViewer().prepare([(DATA, collection()), (MAP, body)])


def test_offline_archive_stores_original_collection_once_and_indexes_each_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction opened the network")

    monkeypatch.setattr(socket, "socket", no_network)
    body = collection(point(), overlay(), point("Other"))
    adapter = source(body)
    directory = tmp_path / adapter.id
    archive = Archive(directory / "archives/fixture")
    try:
        for url, raw, content_type in [
            (DATA, body, "application/vnd.geo+json"),
            (MAP, MAP_BODY, "text/html"),
        ]:
            archive.add(url)
            archive.save(url, 200, raw, content_type, {})
        archive.mark_complete(adapter.id)
        snapshot = publish(adapter, archive, directory)
        entities, html, responses = collect_artifacts(tmp_path, [adapter.id])
        assert len(entities) == len(html) == 2 and len(responses) == 1
        assert next(iter(responses.values())) == body
        assert len({e["evidence"][0]["id"] for e in entities}) == 2
        for entity in entities:
            page = entity["evidence"][0]
            assert page["html_origin"] == "record-rendered"
            assert "body_base64" not in page["source_response"]
            assert page["source_response"]["body_member"] in responses
            assert (
                len(page["records"]) == 1
                and page["records"][0]["geometry"]["type"] == "Point"
            )
        page = entities[0]["evidence"][0]
        (snapshot / "html" / f"{page['id']}.html").write_text("Corrupted")
        with pytest.raises(ValueError, match="HTML checksum"):
            collect(tmp_path, [adapter.id])
        publish(adapter, archive, directory)
        captured = next(p for p in archive.pages("saved") if p["url"] == DATA)
        (archive.path / captured["file"]).write_bytes(b"corrupt JSON")
        with pytest.raises(ValueError, match="checksum"):
            collect(tmp_path, [adapter.id])
    finally:
        archive.close()


def test_overlay_edits_trigger_content_change_but_json_format_and_feature_order_do_not() -> (
    None
):
    adapter = source()
    original = adapter.extract(DATA, collection(), [])[0].metadata(adapter.id)
    reordered = json.dumps(
        json.loads(collection(overlay(), point())), sort_keys=True, indent=2
    ).encode()
    reversed_record = adapter.extract(DATA, reordered, [])[0].metadata(adapter.id)
    assert content_digest([original]) == content_digest([reversed_record])
    line = overlay()
    line["geometry"]["coordinates"][0][0] += 1
    changed = adapter.extract(DATA, collection(point(), line), [])[0].metadata(
        adapter.id
    )
    assert original["id"] == changed["id"]
    assert content_digest([original]) != content_digest([changed])
