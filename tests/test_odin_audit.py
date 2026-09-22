import json
from copy import deepcopy

import pytest

from pipelines.sources.odin import HOST, SUBNAV, Odin, page_url
from pipelines.sources.odin.content import render


def record(number: int, *, images: bool = True) -> dict:
    return {
        "identifier": f"{number:032x}",
        "host": HOST,
        "contentType": "WegCard",
        "live": True,
        "archived": False,
        "name": f"Example {number}",
        "title": f"Example {number}",
        "notes": "A complete description.\nSecond paragraph with <5 m accuracy.",
        "domain": [{"land": "Land"}, {"radars": "Radar Systems"}],
        "origin": [{"origin": "Example country"}],
        "proliferation": [],
        "sections": json.dumps(
            [
                {
                    "name": "Performance",
                    "properties": [{"name": "Range", "value": "80", "units": "km"}],
                    "sections": [
                        {
                            "name": "Accuracy",
                            "properties": [{"name": "Error", "value": "Unknown"}],
                        }
                    ],
                },
            ]
        ),
        "images": json.dumps(
            [
                {
                    "name": "Side photograph",
                    "url": "/dA/" + "a" * 32 + "/fileAsset/a.jpg",
                },
                {
                    "name": "Front photograph",
                    "url": "/dA/" + "b" * 32 + "/fileAsset/b.png",
                },
            ]
            if images
            else []
        ),
    }


def encoded(records: list[dict], total: int) -> bytes:
    return json.dumps(
        {
            "entity": {
                "resultsSize": total,
                "jsonObjectView": {"contentlets": records},
            },
            "errors": [],
        }
    ).encode()


def snapshot(
    count: int = 2,
) -> tuple[Odin, list[dict], dict[str, bytes], dict[str, bytes]]:
    source = Odin()
    records = [record(index + 1, images=index != 1) for index in range(count)]
    responses = {
        page_url(offset): encoded(records[offset : offset + 100], count)
        for offset in range(0, count, 100)
    }
    responses[SUBNAV] = json.dumps(
        {
            "content": {
                "children": {
                    "domain": {
                        "children": {
                            "land": {
                                "key": "land",
                                "name": "Land",
                                "children": {
                                    "radars": {
                                        "key": "radars",
                                        "name": "Radar Systems",
                                        "children": {},
                                    },
                                    "unused": {
                                        "key": "unused",
                                        "name": "Unpopulated Type",
                                        "children": {},
                                    },
                                },
                            },
                        }
                    }
                }
            }
        }
    ).encode()
    source.prepare(responses.items())
    entities = []
    html = {}
    for url, body in responses.items():
        for item in source.extract(url, body, []):
            entity = item.metadata(source.id)
            page = entity["evidence"][0]
            html[page["id"]] = page.pop("rendered_html").encode()
            page["markdown"] = (
                f"# {page['title']}\n\nSource: {page['canonical_url']}\n\n"
                f"{page['attribution']}\n\n{page['markdown']}\n"
            )
            entities.append(entity)
    return source, entities, responses, html


def test_audit_complete_catalog_checks_nested_content_and_all_images() -> None:
    source, entities, responses, html = snapshot()
    report = source.audit(entities, responses, html)
    assert report["complete_equipment_catalog"] is True
    assert report["catalog_pages"] == 1
    assert report["equipment_records"] == 2
    assert report["equipment_by_domain"] == {"Land": 2}
    assert report["equipment_by_category"] == {"Land": 2, "Radar Systems": 2}
    assert report["equipment_by_type"] == {"Land / Radar Systems": 2}
    assert report["catalog_category_count"] == 3
    assert report["catalog_categories_without_equipment"] == ["Land / Unpopulated Type"]
    assert report["sections_checked"] == 4
    assert report["facts_checked"] == 8
    assert report["images_declared"] == report["unique_image_urls"] == 2
    assert report["equipment_without_images"] == [f"{2:032x}"]


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "orphan"])
def test_audit_rejects_missing_duplicate_or_orphan_equipment(mutation: str) -> None:
    source, entities, responses, html = snapshot()
    if mutation == "missing":
        entities.pop()
    elif mutation == "duplicate":
        entities.append(deepcopy(entities[0]))
    else:
        entities[0]["source_id"] = "f" * 32
    with pytest.raises(ValueError, match="equipment IDs"):
        source.audit(entities, responses, html)


@pytest.mark.parametrize(
    "mutation", ["missing_page", "truncated", "duplicate", "unstable_total"]
)
def test_audit_rejects_pagination_coverage_errors(mutation: str) -> None:
    source, entities, responses, html = snapshot(101)
    if mutation == "missing_page":
        responses.pop(page_url(100))
    else:
        page = json.loads(responses[page_url(100)])
        if mutation == "truncated":
            page["entity"]["jsonObjectView"]["contentlets"] = []
        elif mutation == "duplicate":
            page["entity"]["jsonObjectView"]["contentlets"] = [record(1)]
        else:
            page["entity"]["resultsSize"] = 102
            page["entity"]["jsonObjectView"]["contentlets"].append(record(102))
        responses[page_url(100)] = json.dumps(page).encode()
    with pytest.raises(ValueError, match="ODIN"):
        source.audit(entities, responses, html)


@pytest.mark.parametrize(
    "mutation", ["records", "facts", "markdown", "html", "canonical", "orphan_html"]
)
def test_audit_rejects_lost_or_misattributed_source_evidence(mutation: str) -> None:
    source, entities, responses, html = snapshot()
    page = entities[0]["evidence"][0]
    if mutation == "records":
        page["records"][0]["sections"] = "[]"
    elif mutation == "facts":
        entities[0]["facts"].pop()
    elif mutation == "markdown":
        page["markdown"] = page["markdown"].replace("Unknown", "")
    elif mutation == "html":
        html[page["id"]] = html[page["id"]].replace(b"Unknown", b"")
    elif mutation == "canonical":
        page["canonical_url"] = "https://odin.t2com.army.mil/WEG/Asset/" + "f" * 32
    else:
        html["orphan"] = b"<p>unrelated</p>"
    with pytest.raises(ValueError, match="ODIN"):
        source.audit(entities, responses, html)


def test_audit_detects_omission_shared_by_renderer_and_published_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def incomplete_render(record: dict, *, include_images: bool = True) -> str:
        return render(record, include_images=include_images).replace("Unknown", "")

    monkeypatch.setattr("pipelines.sources.odin.render", incomplete_render)
    source, entities, responses, html = snapshot()
    with pytest.raises(ValueError, match="source content missing"):
        source.audit(entities, responses, html)


def test_audit_detects_facts_omitted_by_extraction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from pipelines.sources.odin.content import record_facts

    monkeypatch.setattr(
        "pipelines.sources.odin.record_facts",
        lambda record, canonical: record_facts(record, canonical)[:-1],
    )
    source, entities, responses, html = snapshot()
    with pytest.raises(ValueError, match="source facts missing"):
        source.audit(entities, responses, html)
