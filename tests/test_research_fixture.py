import copy
import hashlib
import json
import re
from pathlib import Path

import pytest

from pipelines.distribution import canonical
from pipelines.registry import source_names
from pipelines.research import (
    FIELD_NAMES,
    RELATION_TYPES,
    SYMMETRIC,
    normalize,
    source_rules,
    text_key,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures/research.json").read_text(encoding="utf-8")
)


def pair(row: dict) -> tuple[str, str, str]:
    source, target = row["source_id"], row["target_id"]
    if row["type"] in SYMMETRIC:
        source, target = sorted((source, target))
    return source, target, row["type"]


def reviewed_relations() -> dict[tuple[str, str, str], tuple[str, dict]]:
    result = {}
    for owner in source_names():
        for row in source_rules(owner)["relations"]:
            key = pair(row)
            assert key not in result, "Reviewed relation is duplicated across adapters"
            result[key] = owner, row
    return result


@pytest.mark.parametrize(
    "case", FIXTURE["field_cases"], ids=lambda case: case["case_id"]
)
def test_reviewed_source_field_normalization(case: dict) -> None:
    before = copy.deepcopy(case["raw"])
    expected = case["expected"]
    assert normalize(case["field"], case["raw"]) == (
        expected["status"],
        expected["value"],
    )
    assert case["raw"] == before
    rules = source_rules(case["entity_id"].split(":", 1)[0])
    mapping = {text_key(name): field for name, field in rules["fields"].items()}
    assert mapping[text_key(case["raw"]["name"])] == case["field"]
    assert case["locator"]["kind"] == "fact"
    assert isinstance(case["locator"]["index"], int)
    assert case["locator"]["index"] >= 0
    assert re.fullmatch(r"[a-f0-9]{24}", case["evidence_id"])


@pytest.mark.parametrize(
    "expected",
    FIXTURE["relations"],
    ids=lambda row: f"{row['source_id']}-{row['type']}-{row['target_id']}",
)
def test_reviewed_relationship_quotes_and_source_ownership(expected: dict) -> None:
    owner, actual = reviewed_relations()[pair(expected)]
    assert owner == expected["owner"]
    assert hashlib.sha256(canonical(actual)).hexdigest() == expected["assertion_sha256"]
    assert actual["basis"] == "reviewed_source_evidence"
    endpoints = {actual["source_id"], actual["target_id"]}
    assert len(endpoints) == 2
    assert any(entity.startswith(owner + ":") for entity in endpoints)
    assert {ref["entity_id"] for ref in actual["evidence"]} == endpoints
    assert actual["rationale"].strip()
    refs = [canonical(ref) for ref in actual["evidence"]]
    assert len(set(refs)) == len(refs)
    for ref in actual["evidence"]:
        assert set(ref) == {"entity_id", "evidence_id", "quote"}
        assert re.fullmatch(r"[a-f0-9]{24}", ref["evidence_id"])
        assert ref["quote"].strip() and len(ref["quote"].encode()) <= 16384


@pytest.mark.parametrize(
    "case", FIXTURE["negative_relations"], ids=lambda case: case["case_id"]
)
def test_ambiguous_source_records_are_not_equated(case: dict) -> None:
    forbidden = {**case, "type": case["forbidden_type"]}
    assert pair(forbidden) not in reviewed_relations()
    assert case["forbidden_type"] == "equivalent"
    assert case["rationale"].strip()
    assert {ref["entity_id"] for ref in case["evidence"]} == {
        case["source_id"],
        case["target_id"],
    }


@pytest.mark.parametrize("source", sorted(FIXTURE["source_coverage"]))
def test_recorded_source_coverage_has_explicit_mapping_outcomes(source: str) -> None:
    observed = FIXTURE["source_coverage"][source]
    rules = source_rules(source)
    fields = set(rules["fields"].values())
    assert fields <= FIELD_NAMES
    assert set(observed["field_counts"]) == fields
    assert observed["mapped_source_facts"] == sum(observed["field_counts"].values())
    assert 0 <= observed["mapped_entities"] <= observed["entities"]
    assert observed["reviewed_relations"] == len(rules["relations"])
    assert all(count > 0 for count in observed["field_counts"].values())
    if not fields:
        assert observed["mapped_source_facts"] == observed["mapped_entities"] == 0


def test_pilot_scope_and_unmapped_semantics_remain_explicit() -> None:
    assert len(FIXTURE["source_coverage"]) == 11
    assert set(FIXTURE["source_coverage"]) <= set(source_names())
    assert len(FIXTURE["field_cases"]) == 15
    assert len(FIXTURE["relations"]) == 13
    assert len(FIXTURE["negative_relations"]) == 8
    assert {pair(row) for row in FIXTURE["relations"]} == set(reviewed_relations())
    assert {row["type"] for row in FIXTURE["relations"]} == RELATION_TYPES
    assert (
        sum(row["mapped_source_facts"] for row in FIXTURE["source_coverage"].values())
        == 18723
    )
    assert re.fullmatch(r"[a-f0-9]{64}", FIXTURE["source_content_sha256"])
    for case in FIXTURE["unmapped_facts"]:
        names = {text_key(name) for name in source_rules(case["source"])["fields"]}
        assert not names.intersection(text_key(name) for name in case["labels"])
    planned = next(
        row
        for row in FIXTURE["field_cases"]
        if row["case_id"] == "reported-planned-ioc"
    )
    assert "development_status" in planned["expected"]["caveat"]
