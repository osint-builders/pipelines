from collections import Counter
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

from pipelines.sources.cambridgepixel import (
    SEED,
    catalog,
    identity,
    reference_url,
    table_records,
)
from pipelines.sources.html import text

if TYPE_CHECKING:
    from pipelines.sources.cambridgepixel import CambridgePixel


def reconcile(supplied: bytes, captured: bytes) -> dict:
    before = {r["id"]: r for r in table_records(supplied)}
    after = {r["id"]: r for r in table_records(captured)}
    changes = []
    for key in before.keys() & after.keys():
        left, right = dict(before[key]), dict(after[key])
        for row in (left, right):
            row["urls"] = [reference_url(url) for url in row["urls"]]
        differences = {
            field: {"supplied": left[field], "captured": right[field]}
            for field in left
            if left[field] != right[field]
        }
        if differences:
            changes.append({"id": key, "fields": differences})
    return {
        "supplied": len(before),
        "captured": len(after),
        "matched": len(before.keys() & after.keys()),
        "missing": [before[k] for k in sorted(before.keys() - after.keys())],
        "added": [after[k] for k in sorted(after.keys() - before.keys())],
        "changes": sorted(changes, key=lambda row: row["id"]),
    }


def audit_snapshot(
    source: "CambridgePixel",
    entities: list[dict],
    responses: dict[str, bytes],
    html: dict[str, bytes],
) -> dict:
    dataset, products, _ = catalog(responses[SEED])
    expected = {
        entity.key: entity for entity in source.extract(SEED, responses[SEED], [])
    }
    actual = {entity["source_id"]: entity for entity in entities}
    if len(actual) != len(entities) or actual.keys() != expected.keys():
        raise ValueError("Cambridge Pixel records are missing, duplicated, or orphaned")
    if not set(source.seeds).issubset(responses):
        raise ValueError("Reviewed radar source pages are missing")
    checked = 0
    for key, base in expected.items():
        entity = actual[key]
        page = next(p for p in entity["evidence"] if p["url"] == SEED)
        if page["records"] != base.evidence[0].records:
            raise ValueError("Original radar record changed during extraction")
        for fact in base.facts:
            if not any(
                f["name"] == fact.name
                and f["raw"] == fact.raw
                and f["evidence"] == SEED
                for f in entity["facts"]
            ):
                raise ValueError("Cambridge Pixel field missing from source facts")
            checked += 1
        rendered = " ".join(
            text(BeautifulSoup(html[page["id"]], "html.parser")).split()
        )
        if base.evidence[0].records[0]["description"] not in rendered:
            raise ValueError("Radar description missing from rendered evidence")
    from pipelines.sources.cambridgepixel.imagery import validate_page

    for url in source.seeds[1:]:
        validate_page(url, responses[url], source.image_matches)
    reviewed = source.image_reviews
    reviewed_ids = {identity(r["manufacturer"], r["model"]) for r in reviewed}
    if len(reviewed_ids) != len(reviewed) or not reviewed_ids.issubset(expected):
        raise ValueError(
            "Radar image reviews are duplicated or absent from the catalog"
        )
    return {
        "catalog_rows": len(products),
        "checked_facts": checked,
        "database_updated": dataset["dateModified"],
        "image_review": {
            "reviewed": len(reviewed),
            "states": dict(Counter(row["status"] for row in reviewed)),
            "ambiguous": sum(bool(row.get("ambiguous")) for row in reviewed),
            "remaining": len(expected.keys() - reviewed_ids),
        },
    }
