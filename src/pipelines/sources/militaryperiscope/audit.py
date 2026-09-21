from collections import Counter

from bs4 import BeautifulSoup

from pipelines.sources.militaryperiscope import SUBJECTS, MilitaryPeriscope, payload


def text(value: str | bytes) -> str:
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ").split())


def content_fragments(blocks: list) -> list[str]:
    result = []
    for block in blocks:
        kind, value = block["type"], block["value"]
        if kind in {"content", "paragraph"}:
            result.append(value)
        elif kind == "table":
            result.extend(cell for row in value["data"] for cell in row if cell)
        elif kind == "image":
            result.extend(
                value[key] for key in ("title", "source", "caption") if value.get(key)
            )
        elif kind == "images":
            result.extend(
                content_fragments(
                    [{"type": "image", "value": item["image"]} for item in value]
                )
            )
        elif isinstance(value, list):
            result.extend(content_fragments(value))
        else:
            result.extend(
                value[key] for key in ("header", "subheader", "style") if value.get(key)
            )
            result.extend(content_fragments(value["body"]))
    return result


def audit_snapshot(
    source: MilitaryPeriscope,
    entities: list[dict],
    responses: dict[str, bytes],
    html: dict[str, bytes],
) -> dict:
    expected = {
        str(node["id"])
        for node in source.nodes.values()
        if node["page_type"] in SUBJECTS
    }
    if expected != {entity["source_id"] for entity in entities}:
        raise ValueError("Trial subjects missing or duplicated")
    actual_pages = {page["url"] for entity in entities for page in entity["evidence"]}
    if actual_pages != source.subjects.keys() - source.restricted:
        raise ValueError("Full sections missing")
    fragments_checked = 0
    for entity in entities:
        for page in entity["evidence"]:
            _, props = payload(responses[page["url"]])
            retained = text(html[page["id"]])
            for fragment in content_fragments(props.get("section") or props["content"]):
                if text(fragment) not in retained:
                    raise ValueError(f"Missing content in {entity['id']}")
                fragments_checked += 1
    return {
        "catalog_nodes": len(source.nodes),
        "content_fragments_checked": fragments_checked,
        "subjects_by_type": dict(
            Counter(
                node["page_type"]
                for node in source.nodes.values()
                if node["page_type"] in SUBJECTS
            )
        ),
        "subscription_only_sections": sorted(source.restricted),
        "complete_accessible_trial": True,
    }
