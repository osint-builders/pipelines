import json
import socket
from copy import deepcopy
from html import escape
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipelines.archive import Archive
from pipelines.build import publish
from pipelines.distribution import collect_artifacts, content_digest
from pipelines.sources.cambridgepixel import (
    HEADERS,
    MANUFACTURER_ALIASES,
    SEED,
    CambridgePixel,
    identity,
    table_records,
)


def product(manufacturer: str = "Example", model: str = "Watchman") -> dict:
    return {
        "@type": "ProductModel",
        "name": manufacturer + " " + model,
        "manufacturer": {"@type": "Organization", "name": manufacturer},
        "brand": {"@type": "Organization", "name": manufacturer},
        "model": model,
        "description": "Example surveillance radar, approximately 20 km in trials.",
        "url": "https://manufacturer.test/product/",
        "additionalProperty": [
            {"@type": "PropertyValue", "name": "Band", "value": "X/S"},
            {"@type": "PropertyValue", "name": "Status", "value": "Legacy"},
            {
                "@type": "PropertyValue",
                "name": "Applications",
                "value": "Marine, Naval",
            },
        ],
    }


def page(items: list[dict] | None = None) -> bytes:
    items = items if items is not None else [product(), product("Other maker")]
    dataset = {
        "@type": "Dataset",
        "@id": SEED + "#dataset",
        "dateModified": "2026-08-07",
    }
    listing = {
        "@type": "ItemList",
        "@id": SEED + "#itemlist",
        "numberOfItems": len(items),
        "itemListElement": [
            {"@type": "ListItem", "position": i + 1, "item": p}
            for i, p in enumerate(items)
        ],
    }
    rows = []
    for p in items:
        values = {v["name"]: v["value"] for v in p["additionalProperty"]}
        columns = [
            p["manufacturer"]["name"],
            p["model"],
            values["Band"],
            values["Status"],
        ]
        cells = "".join(
            f'<td><span class="show-mobile">{label}: </span>{escape(value)}</td>'
            for label, value in zip(HEADERS, columns)
        )
        apps = "".join(
            f'<li class="database-app"><img src="/icon.jpg"><span>{escape(a)}</span></li>'
            for a in values["Applications"].split(", ")
        )
        link = (
            f'<a href="{escape(p["url"])}">Visit Website</a>'
            if p.get("url")
            else "Unavailable"
        )
        rows.append(
            f'<tr>{cells}<td>{escape(p["description"])}<ul class="database-app-list">{apps}</ul></td><td>{link}</td></tr>'
        )
    table = (
        "<table><thead><tr>"
        + "".join(f"<th>{name}</th>" for name in HEADERS)
        + "</tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table>"
    )
    return (
        f'<link rel="canonical" href="{SEED}"><main><h1>Radar Database</h1><p>{len(items)} radar models found.</p>{table}<table><thead><tr><th>Frequency guide</th></tr></thead><tbody><tr><td>Not a model</td></tr></tbody></table><p>The radar data is mostly collated from manufacturers\' websites and may not be accurate or up to date.</p></main><footer>Copyright Cambridge Pixel Ltd. 2026</footer>'
        + "".join(
            f'<script type="application/ld+json">{json.dumps(s)}</script>'
            for s in (dataset, listing)
        )
    ).encode()


def test_single_page_scope_excludes_filters_guides_and_manufacturer_crawling() -> None:
    adapter = CambridgePixel()
    assert adapter.normalize(SEED.rstrip("/")) == SEED
    assert adapter.normalize(SEED + "#itemlist") == SEED
    assert adapter.discover(SEED, page()) == []
    assert adapter.labels(SEED, page()) == {}
    for url in [
        SEED + "?status_filter=Legacy",
        SEED + "model/",
        SEED.replace("cambridgepixel.com", "www.cambridgepixel.com"),
        "https://manufacturer.test/product/",
        "https://cambridgepixel.com/search/",
        SEED.replace("https://", "https://user@"),
    ]:
        assert adapter.normalize(url) is None
    assert len(adapter.extract(SEED, page(), [])) == 2


def test_model_identity_preserves_distinct_manufacturers_and_full_family_records() -> (
    None
):
    items = [product(), product("Other maker"), product("Example", "Series 100 / 200")]
    entities = CambridgePixel().extract(SEED, page(items), [])
    assert len({e.key for e in entities}) == 3
    assert identity(" Example ", "WATCHMAN") == identity("Example", "Watchman")
    assert {e.evidence[0].records[0]["model"] for e in entities} == {
        "Watchman",
        "Series 100 / 200",
    }
    assert all(e.aliases == [e.evidence[0].records[0]["model"]] for e in entities)
    assert all("approximately 20 km" in e.evidence[0].markdown for e in entities)
    assert all(not fact.values for e in entities for fact in e.facts)
    assert len({e.evidence[0].id for e in entities}) == 3
    changed = deepcopy(items)
    changed[0]["description"] += " Revised description."
    assert {e.key for e in CambridgePixel().extract(SEED, page(changed), [])} == {
        e.key for e in entities
    }
    with pytest.raises(ValueError, match="Duplicate"):
        CambridgePixel().extract(SEED, page([product(), product()]), [])


def test_passive_esm_sensor_missing_link_and_raw_unknown_band_survive() -> None:
    p = product("ERA a.s.", "VERA-NG")
    p["description"] = "Passive ESM tracker using TDOA."
    p["additionalProperty"][0]["value"] = "Passive ESM (VHF-Ku)"
    p.pop("url")
    entity = CambridgePixel().extract(SEED, page([p]), [])[0]
    assert entity.kind == "sensor" and not entity.evidence[0].links
    assert entity.evidence[0].records == [p]
    assert "Passive ESM (VHF-Ku)" in entity.evidence[0].search_text
    p["additionalProperty"][0]["value"] = "Not stated"
    assert CambridgePixel().extract(SEED, page([p]), [])[0].facts[2].raw == "Not stated"


@pytest.mark.parametrize(
    "change",
    [
        "count",
        "missing-row",
        "description",
        "band",
        "applications",
        "link",
        "missing-schema",
        "position",
        "identity",
        "date",
        "notice",
    ],
)
def test_partial_or_disagreeing_catalogs_fail(change: str) -> None:
    soup = BeautifulSoup(page(), "html.parser")
    if change == "count":
        soup.select("main p")[0].string = "400 radar models found."
    elif change == "missing-row":
        soup.select("tbody tr")[0].decompose()
    elif change in {"description", "band", "applications", "link"}:
        row = soup.select("tbody tr")[0]
        if change == "description":
            row.select("td")[4].insert(0, "Contradiction ")
        if change == "band":
            row.select("td")[2].string = "Wrong band"
        if change == "applications":
            row.select(".database-app span")[0].string = "Wrong application"
        if change == "link":
            row.select("a")[0]["href"] = "https://other.test/"
    elif change == "missing-schema":
        soup.select("script")[-1].decompose()
    elif change in {"position", "identity", "date"}:
        index = -1 if change in {"position", "identity"} else 0
        node = soup.select("script")[index]
        value = json.loads(node.get_text())
        if change == "position":
            value["itemListElement"][0]["position"] = 100
        if change == "identity":
            value["@id"] = "https://other.test/"
        if change == "date":
            value.pop("dateModified")
        node.string = json.dumps(value)
    else:
        soup.select("main p")[-1].decompose()
    with pytest.raises(ValueError):
        CambridgePixel().extract(SEED, str(soup).encode(), [])


def test_referral_parameters_do_not_hide_functional_link_changes() -> None:
    item = product()
    item["url"] = "https://manufacturer.test/product/?id=42&empty="
    soup = BeautifulSoup(page([item]), "html.parser")
    link = soup.select_one("tbody a")
    assert link is not None
    link["href"] = (
        item["url"]
        + "&utm_source=cambridgepixel.com&utm_medium=referral&utm_campaign=radar-database"
    )
    assert len(CambridgePixel().extract(SEED, str(soup).encode(), [])) == 1
    link["href"] = str(link["href"]).replace("id=42", "id=43")
    with pytest.raises(ValueError, match="disagree"):
        CambridgePixel().extract(SEED, str(soup).encode(), [])


def test_pasted_table_retains_every_field_and_strips_mobile_labels_and_icons() -> None:
    soup = BeautifulSoup(page([product()]), "html.parser")
    table = soup.select_one("table")
    assert table is not None
    row = table_records(
        str(table).replace("surveillance radar", "surveillance\n    radar").encode()
    )[0]
    assert row == {
        "id": identity("Example", "Watchman"),
        "manufacturer": "Example",
        "model": "Watchman",
        "band": "X/S",
        "status": "Legacy",
        "description": product()["description"],
        "applications": ["Marine", "Naval"],
        "urls": ["https://manufacturer.test/product/"],
    }
    with pytest.raises(ValueError, match="ambiguous"):
        table_records((str(table) * 2).encode())


@pytest.mark.parametrize("current,previous", MANUFACTURER_ALIASES.items())
def test_manufacturer_renames_preserve_ids_and_original_labels(
    current: str, previous: str
) -> None:
    before = CambridgePixel().extract(SEED, page([product(previous)]), [])[0]
    after = CambridgePixel().extract(SEED, page([product(current)]), [])[0]
    assert before.key == after.key
    assert before.evidence[0].id == after.evidence[0].id
    assert after.evidence[0].records[0]["manufacturer"]["name"] == current
    assert after.title.startswith(current)


def test_order_layout_and_transport_changes_do_not_change_content_but_record_edits_do() -> (
    None
):
    adapter = CambridgePixel()

    def digest(body: bytes) -> str:
        return content_digest(
            [e.metadata(adapter.id) for e in adapter.extract(SEED, body, [])]
        )

    a = [product(), product("Other maker")]
    original = digest(page(a))
    changed = (
        page(list(reversed(a)))
        .replace(b"<td>", b'<td class="new-layout">')
        .replace(b"</footer>", b" New menu.</footer>")
    )
    assert digest(changed) == original
    a[0]["description"] += " Updated capability."
    assert digest(page(a)) != original


def test_offline_record_publication_shares_complete_html_capture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction tried the network")

    monkeypatch.setattr(socket, "socket", no_network)
    adapter = CambridgePixel()
    adapter.minimum_entities = 2
    directory = tmp_path / adapter.id
    archive = Archive(directory / "archives/fixture")
    body = page()
    try:
        archive.add(SEED)
        archive.save(SEED, 200, body, "text/html; charset=utf-8", {})
        archive.mark_complete(adapter.id)
        snapshot = publish(adapter, archive, directory)
        entities, html, responses = collect_artifacts(tmp_path, [adapter.id])
        assert len(entities) == len(html) == 2 and len(responses) == 1
        member, raw = next(iter(responses.items()))
        assert member.endswith(".html") and raw == body
        for entity in entities:
            evidence = entity["evidence"][0]
            assert evidence["source_response"]["body_member"] == member
            assert evidence["html_origin"] == "record-rendered"
            assert (
                len(evidence["records"]) == 1
                and "body_base64" not in evidence["source_response"]
            )
        (snapshot / "html" / f"{entities[0]['evidence'][0]['id']}.html").write_text(
            "corrupt"
        )
        with pytest.raises(ValueError, match="checksum"):
            collect_artifacts(tmp_path, [adapter.id])
    finally:
        archive.close()
