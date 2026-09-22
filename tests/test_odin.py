import json
from copy import deepcopy

import pytest
from bs4 import BeautifulSoup

from pipelines.model import EntityKind
from pipelines.sources.odin import (
    API,
    HOST,
    ORIGIN,
    PAGE_SIZE,
    QUERY,
    SORT,
    SUBNAV,
    Odin,
    canonical_url,
    category_tree,
    entity_kind,
    page_url,
    payload,
    validate_pages,
)
from pipelines.sources.odin.content import record_facts, render, sections


def record(number: int = 1) -> dict:
    return {
        "identifier": f"{number:032x}",
        "name": "Example-2 search radar",
        "title": "Example-2 search radar",
        "contentType": "WegCard",
        "host": HOST,
        "live": True,
        "archived": False,
        "notes": "The complete description.\nA second paragraph <script>alert(1)</script>.",
        "domain": [{"radar": "Radar Systems"}, {"land": "Land"}],
        "origin": [{"a": "Country A"}],
        "proliferation": [{"b": "Country B"}],
        "dateOfIntroduction": "1998-01-01 12:00:00.0",
        "disname": "Radar Example 2",
        "disstring": "01.02.03.04.05.06.07",
        "publishDate": "2025-01-02 00:00:00.0",
        "images": json.dumps(
            [
                {
                    "name": "Example photo",
                    "url": "/dA/" + "a" * 32 + "/fileAsset/photo.jpg",
                }
            ]
        ),
        "sections": json.dumps(
            [
                {
                    "name": "System",
                    "properties": [
                        {"name": "Alternate Designation(s)", "value": "E-2; Example II"}
                    ],
                },
                {
                    "name": "Sensor",
                    "sections": [
                        {
                            "name": "Radar",
                            "properties": [
                                {"name": "Range", "value": "80-120", "units": "km"},
                                {"name": "Height", "value": 0, "units": "m"},
                            ],
                        },
                        {
                            "name": "Optical",
                            "properties": [
                                {"name": "Range", "value": "10", "units": "km"}
                            ],
                        },
                    ],
                },
            ]
        ),
        "ownerUserName": "Not searchable",
        "internalAdministration": {"retained": True},
    }


def response(records: list[dict], total: int | None = None) -> bytes:
    return json.dumps(
        {
            "entity": {
                "resultsSize": len(records) if total is None else total,
                "jsonObjectView": {"contentlets": records},
            }
        }
    ).encode()


def subnav() -> bytes:
    return json.dumps(
        {
            "content": {
                "name": "WEG",
                "children": {
                    "domain": {
                        "children": {
                            "land": {"name": "Land", "key": "land", "children": {}}
                        }
                    }
                },
            }
        }
    ).encode()


def test_request_scope_and_body() -> None:
    source = Odin()
    assert source.seeds == (page_url(0), SUBNAV)
    assert source.normalize(page_url(100)) == page_url(100)
    body = source.request_body(page_url(100))
    assert body is not None
    assert json.loads(body) == {
        "limit": 100,
        "offset": 100,
        "query": QUERY,
        "sort": SORT,
    }
    assert source.request_body(SUBNAV) is None
    assert source.request_headers(page_url(0))["Content-Type"] == "application/json"
    assert not any(
        "cookie" in key.lower() for key in source.request_headers(page_url(0))
    )
    assert source.discover(SUBNAV, subnav()) == []


@pytest.mark.parametrize(
    "url",
    [
        API,
        API + "?offset=0&limit=100",
        page_url(0) + "&query=other",
        page_url(0) + "#fragment",
        page_url(0).replace("https", "http"),
        page_url(0).replace("offset=0", "offset=1"),
        page_url(0).replace("offset=0", "offset=00"),
        ORIGIN + "/WEG/Asset/" + "a" * 32,
        "https://evil.example/",
        SUBNAV + "/",
    ],
)
def test_rejects_noncanonical_requests(url: str) -> None:
    source = Odin()
    assert source.normalize(url) is None
    with pytest.raises(ValueError):
        source.request_body(url)


def test_pagination_discovers_every_page_and_prepares_offline() -> None:
    first = response([record(i) for i in range(1, PAGE_SIZE + 1)], 201)
    second = response([record(i) for i in range(101, 201)], 201)
    last = response([record(201)], 201)
    source = Odin()
    assert source.discover(page_url(0), first) == [page_url(100), page_url(200)]
    assert source.discover(page_url(100), second) == []
    pages = [
        (page_url(200), last),
        (SUBNAV, subnav()),
        (page_url(0), first),
        (page_url(100), second),
    ]
    source.prepare(pages)
    assert len(source.extract(page_url(0), first, [])) == 100
    assert source.extract(SUBNAV, subnav(), []) == []
    assert set(validate_pages(pages)) == {0, 100, 200}


@pytest.mark.parametrize(
    "failure",
    [
        "missing",
        "duplicate_page",
        "overlap",
        "unsorted",
        "changed_total",
        "short_page",
        "extra_page",
    ],
)
def test_pagination_fails_closed(failure: str) -> None:
    first = [record(i) for i in range(1, 101)]
    last = [record(101)]
    pages = [(page_url(0), response(first, 101)), (page_url(100), response(last, 101))]
    if failure == "missing":
        pages.pop()
    elif failure == "duplicate_page":
        pages.append(pages[0])
    elif failure == "overlap":
        pages[1] = (page_url(100), response([record(100)], 101))
    elif failure == "unsorted":
        pages[0] = (page_url(0), response(list(reversed(first)), 101))
    elif failure == "changed_total":
        pages[1] = (page_url(100), response(last, 102))
    elif failure == "short_page":
        pages[0] = (page_url(0), response(first[:-1], 101))
    else:
        pages.append((page_url(200), response([], 101)))
    with pytest.raises(ValueError):
        validate_pages(pages)


def test_full_record_identity_nested_facts_and_safe_rendering() -> None:
    source = Odin()
    original = record()
    body = response([original])
    source.prepare([(SUBNAV, subnav()), (page_url(0), body)])
    entity = source.extract(page_url(0), body, ["unrelated page title"])[0]
    evidence = entity.evidence[0]
    assert entity.kind is EntityKind.RADAR
    assert (
        entity.url
        == canonical_url(original)
        == ORIGIN + "/WEG/Asset/" + original["identifier"]
    )
    assert evidence.url == page_url(0)
    assert evidence.record_id == original["identifier"]
    assert evidence.records == [original]
    assert entity.categories == ["Land", "Radar Systems"]
    assert {"E-2", "Example II", "Radar Example 2"}.issubset(entity.aliases)
    facts = {fact.name: fact for fact in entity.facts}
    assert facts["Sensor / Radar / Range"].raw == "80-120 km"
    assert facts["Sensor / Optical / Range"].raw == "10 km"
    assert facts["Sensor / Radar / Height"].raw == "0 m"
    assert facts["Origin"].raw == "Country A"
    assert all(fact.evidence == entity.url for fact in entity.facts)
    assert "Not searchable" not in evidence.markdown
    assert "internalAdministration" not in evidence.markdown
    soup = BeautifulSoup(evidence.rendered_html, "html.parser")
    assert not soup.select("script")
    assert "<script>alert(1)</script>" in soup.get_text()
    assert "The complete description." in evidence.markdown
    assert "Example photo" in evidence.markdown
    assert "Example photo" not in evidence.search_text
    image = soup.select_one("img")
    assert image is not None
    assert image["src"] == ORIGIN + "/dotcms/dA/" + "a" * 32 + "/fileAsset/photo.jpg"
    assert entity.metadata("odin")["source_id"] == original["identifier"]
    assert evidence.rendered_html == render(original)


def test_requires_complete_prepared_snapshot_and_unchanged_page() -> None:
    source = Odin()
    body = response([record()])
    with pytest.raises(ValueError, match="complete unchanged"):
        source.extract(page_url(0), body, [])
    with pytest.raises(ValueError, match="category hierarchy"):
        source.prepare([(page_url(0), body)])
    source.prepare([(SUBNAV, subnav()), (page_url(0), body)])
    changed = record()
    changed["notes"] += " Changed"
    with pytest.raises(ValueError, match="complete unchanged"):
        source.extract(page_url(0), response([changed]), [])


@pytest.mark.parametrize(
    "field,value",
    [
        ("identifier", "../unsafe"),
        ("identifier", "a" * 31),
        ("live", False),
        ("archived", True),
        ("host", "elsewhere"),
        ("contentType", "OtherType"),
        ("sections", "{bad json"),
        ("sections", {}),
        ("domain", "Land"),
    ],
)
def test_malformed_records_fail_before_extraction(field: str, value: object) -> None:
    item = record()
    item[field] = value
    with pytest.raises(ValueError):
        payload(response([item]))


def test_native_record_ids_remain_distinct_for_identical_titles() -> None:
    source = Odin()
    body = response([record(1), record(2)])
    source.prepare([(SUBNAV, subnav()), (page_url(0), body)])
    entities = source.extract(page_url(0), body, [])
    assert entities[0].title == entities[1].title
    assert entities[0].key != entities[1].key
    assert entities[0].evidence[0].id != entities[1].evidence[0].id


def test_decoded_sections_and_units_are_not_mutated() -> None:
    original = record()
    original["sections"] = json.loads(original["sections"])
    before = deepcopy(original)
    assert len(sections(original)) == 2
    assert record_facts(original, canonical_url(original))
    assert original == before


@pytest.mark.parametrize(
    "labels,expected",
    [
        (["Air", "Aircraft"], EntityKind.AIRCRAFT),
        (["Air", "Aircraft Armament", "Aircraft Missiles"], EntityKind.WEAPON),
        (["Sea", "Naval Watercraft", "Aircraft Carriers"], EntityKind.VESSEL),
        (["Sea", "Naval Watercraft", "Guided Missile Cruisers"], EntityKind.VESSEL),
        (["Land", "Radar Guided Missile Systems"], EntityKind.WEAPON),
        (["Land", "Main Battle Tanks", "Tanks"], EntityKind.VEHICLE),
        (["Land", "Land Based Sensors"], EntityKind.SENSOR),
        (["Land", "Communication Equipment"], EntityKind.EQUIPMENT),
    ],
)
def test_kind_uses_categories_not_title(
    labels: list[str], expected: EntityKind
) -> None:
    item = record()
    item["domain"] = [{str(i): label} for i, label in enumerate(labels)]
    assert entity_kind(item) is expected


def test_invalid_category_hierarchy_fails() -> None:
    with pytest.raises(ValueError):
        category_tree(b"{}")


def test_partial_error_response_is_not_accepted_as_complete() -> None:
    data = json.loads(response([record()]))
    data["errors"] = ["Some records could not be loaded"]
    with pytest.raises(ValueError):
        payload(json.dumps(data).encode())
