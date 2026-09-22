from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup

if TYPE_CHECKING:
    from pipelines.sources.base import Source


def text(value: str | bytes) -> str:
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ").split())


def section_fragments(sections: list[dict]) -> Iterable[str]:
    for section in sections:
        yield section.get("name", "")
        for prop in section.get("properties", []):
            for field in ("name", "value", "units"):
                value = prop.get(field)
                yield "" if value is None else str(value)
        yield from section_fragments(section.get("sections", []))


def section_count(sections: list[dict]) -> int:
    return sum(1 + section_count(section.get("sections", [])) for section in sections)


def category_paths(tree: dict) -> dict[str, set[tuple[str, ...]]]:
    result: dict[str, set[tuple[str, ...]]] = {}

    def visit(nodes: dict, parents: tuple[str, ...]) -> None:
        for key, node in nodes.items():
            path = (*parents, node["name"])
            result.setdefault(key, set()).add(path)
            if variable := node.get("variable"):
                result.setdefault(variable, set()).add(path)
            visit(node["children"], path)

    visit(tree["children"]["domain"]["children"], ())
    return result


def audit_snapshot(
    source: "Source",
    entities: list[dict],
    responses: dict[str, bytes],
    html: dict[str, bytes],
) -> dict:
    from pipelines.sources.odin import (
        SUBNAV,
        canonical_url,
        category_tree,
        page_url,
        validate_pages,
    )
    from pipelines.sources.odin.content import record_facts, sections
    from pipelines.sources.odin.media import image_url, record_images

    pages = validate_pages(responses.items())
    if SUBNAV not in responses:
        raise ValueError("ODIN category hierarchy is missing")
    tree = category_tree(responses[SUBNAV])
    paths = category_paths(tree)
    records = {
        record["identifier"]: (page_url(offset), record)
        for offset, items in pages.items()
        for record in items
    }
    by_id = {entity["source_id"]: entity for entity in entities}
    if len(by_id) != len(entities) or by_id.keys() != records.keys():
        raise ValueError("ODIN equipment IDs are missing, duplicated, or orphaned")
    expected_html = {page["id"] for entity in entities for page in entity["evidence"]}
    if html.keys() != expected_html:
        raise ValueError("ODIN rendered evidence is missing or orphaned")

    categories: Counter[str] = Counter()
    domains: Counter[str] = Counter(
        {node["name"]: 0 for node in tree["children"]["domain"]["children"].values()}
    )
    equipment_types: Counter[str] = Counter()
    represented_paths: set[tuple[str, ...]] = set()
    unknown_categories: set[str] = set()
    checked_ids: set[str] = set()
    image_urls: set[str] = set()
    images_declared = sections_checked = facts_checked = fragments_checked = 0
    without_images: list[str] = []
    for offset in sorted(pages):
        url = page_url(offset)
        for expected in source.extract(url, responses[url], []):
            if expected.key in checked_ids:
                raise ValueError("ODIN extraction repeated equipment records")
            checked_ids.add(expected.key)
            entity = by_id[expected.key]
            record = records[expected.key][1]
            expected_metadata = expected.metadata(source.id)
            for key in (
                "id",
                "source",
                "source_id",
                "title",
                "kind",
                "url",
                "aliases",
                "categories",
                "facts",
            ):
                if entity.get(key) != expected_metadata.get(key):
                    raise ValueError(f"ODIN {key} mismatch: {expected.key}")
            if len(entity["evidence"]) != 1:
                raise ValueError(f"ODIN equipment must have one record: {expected.key}")
            page = entity["evidence"][0]
            rendered = expected.evidence[0].rendered_html.encode()
            expected_page = expected_metadata["evidence"][0]
            for key in (
                "id",
                "url",
                "title",
                "canonical_url",
                "record_id",
                "records",
                "links",
                "search_text",
                "language",
                "attribution",
            ):
                if page.get(key) != expected_page.get(key):
                    raise ValueError(f"ODIN evidence {key} mismatch: {expected.key}")
            if page.get("records") != [record]:
                raise ValueError(f"ODIN source record changed: {expected.key}")
            canonical = canonical_url(record)
            if entity["facts"] != [
                asdict(fact) for fact in record_facts(record, canonical)
            ]:
                raise ValueError(f"ODIN source facts missing: {expected.key}")
            if (
                entity["url"] != canonical
                or page["canonical_url"] != canonical
                or page["url"] != url
            ):
                raise ValueError(f"ODIN source identity mismatch: {expected.key}")
            if html[page["id"]] != rendered:
                raise ValueError(f"ODIN rendered HTML mismatch: {expected.key}")
            markdown = (
                f"# {page['title']}\n\nSource: {canonical}\n\n"
                f"{page['attribution']}\n\n{expected.evidence[0].markdown}\n"
            )
            if page.get("markdown") != markdown:
                raise ValueError(f"ODIN Markdown mismatch: {expected.key}")
            retained = text(rendered)
            content = sections(record)
            fragments = [record.get("name") or record["title"], record.get("notes", "")]
            fragments.extend(section_fragments(content))
            for fragment in fragments:
                normalized = " ".join(str(fragment or "").split())
                if normalized and normalized not in retained:
                    raise ValueError(f"ODIN source content missing: {expected.key}")
                fragments_checked += bool(normalized)
            sections_checked += section_count(content)
            facts_checked += len(entity["facts"])
            labels = {
                label
                for category in record.get("domain", [])
                for label in category.values()
            }
            categories.update(labels)
            keys = {key for category in record.get("domain", []) for key in category}
            selected_paths = {path for key in keys for path in paths.get(key, set())}
            represented_paths.update(
                path[:depth]
                for path in selected_paths
                for depth in range(1, len(path) + 1)
            )
            unknown_categories.update(keys - paths.keys())
            domains.update({path[0] for path in selected_paths})
            equipment_types.update(
                " / ".join(path)
                for path in selected_paths
                if not any(
                    len(other) > len(path) and other[: len(path)] == path
                    for other in selected_paths
                )
            )
            declared = record_images(record)
            images_declared += len(declared)
            declared_urls = {image_url(target) for target, _, _ in declared}
            retained_urls = {
                str(node.get("src") or node.get("href"))
                for node in BeautifulSoup(rendered, "html.parser").select(
                    "img[src], a[href]"
                )
            }
            if not declared_urls <= retained_urls:
                raise ValueError(f"ODIN source images missing: {expected.key}")
            image_urls.update(declared_urls)
            if not declared:
                without_images.append(expected.key)
    if checked_ids != records.keys():
        raise ValueError("ODIN extraction omitted equipment records")
    all_paths = {path for matches in paths.values() for path in matches}
    return {
        "complete_equipment_catalog": True,
        "catalog_pages": len(pages),
        "equipment_records": len(records),
        "equipment_by_domain": dict(sorted(domains.items())),
        "equipment_by_category": dict(sorted(categories.items())),
        "equipment_by_type": dict(sorted(equipment_types.items())),
        "catalog_category_count": len(all_paths),
        "catalog_categories_without_equipment": sorted(
            " / ".join(path) for path in all_paths - represented_paths
        ),
        "uncatalogued_category_keys": sorted(unknown_categories),
        "images_declared": images_declared,
        "unique_image_urls": len(image_urls),
        "equipment_with_images": len(records) - len(without_images),
        "equipment_without_images_count": len(without_images),
        "equipment_without_images": sorted(without_images),
        "sections_checked": sections_checked,
        "facts_checked": facts_checked,
        "content_fragments_checked": fragments_checked,
    }
