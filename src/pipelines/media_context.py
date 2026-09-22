from collections.abc import Iterable


def page_owners(url: str, entities: Iterable[dict]) -> list[tuple[str, str]]:
    """Return unique entity/evidence pairs captured from this page."""
    return sorted(
        {
            (entity["id"], evidence["id"])
            for entity in entities
            for evidence in entity["evidence"]
            if evidence["url"] == url
        }
    )


def eligible_reference(record: dict, reference: dict) -> bool:
    """Keep reference context only when its recorded occurrence permits it."""
    occurrences = record.get("occurrences", [])
    if not occurrences:
        return True
    pair = (reference["entity_id"], reference["evidence_id"])
    contexts = [
        item
        for item in occurrences
        if pair
        in {
            (ref["entity_id"], ref["evidence_id"])
            for ref in item.get("references", record["references"])
        }
    ]
    active = [
        item
        for item in contexts
        if item.get("associated") and not item.get("exclusion_reason")
    ]
    if not active:
        return False

    def full(ref: dict) -> dict:
        return {
            "entity_id": ref["entity_id"],
            "evidence_id": ref["evidence_id"],
            "caption": ref.get("caption", ""),
            "section": ref.get("section", ""),
            "association": ref.get("association", "source_context"),
            "ambiguous": ref.get("ambiguous", False),
        }

    rich = [
        [
            full(ref)
            for ref in item.get("reference_contexts", [])
            if (ref["entity_id"], ref["evidence_id"]) == pair
        ]
        for item in active
    ]
    if any(full(reference) in refs for refs in rich):
        return True
    if all(rich):
        return False
    if any(
        full(reference) == full(ref)
        for item in contexts
        if not item.get("associated") or item.get("exclusion_reason")
        for ref in item.get("reference_contexts", [])
    ):
        return False

    def matches(item: dict) -> bool:
        matched = False
        for field in ("caption", "section"):
            left, right = reference.get(field, ""), item.get(field, "")
            if left and right:
                if left != right:
                    return False
                matched = True
        return matched

    if any(matches(item) for item in active):
        return True
    # Older occurrences retain only owner pairs; supplementary per-reference
    # context remains valid unless the recorded excluded context identifies it.
    return not any(
        matches(item)
        for item in contexts
        if not item.get("associated") or item.get("exclusion_reason")
    )
