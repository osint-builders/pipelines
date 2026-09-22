import io
import json
import zipfile
from copy import deepcopy

import pytest
from accept_cli import accept_research


def research_acceptance_fixture() -> tuple[bytes, dict, dict, dict]:
    manifest: dict = {
        "dataset_id": "research-fixture",
        "research": {
            "fields": [
                {"name": "manufacturer", "kind": "text", "unit": ""},
                {"name": "range", "kind": "number", "unit": "m"},
            ]
        },
    }
    info = {"research_available": True, "research": manifest["research"]}
    index: list[dict] = [
        {
            "id": f"sample:{name}",
            "source": "sample",
            "kind": "radar",
            "title": name,
            "url": f"https://sample.test/{name}",
            "categories": ["radars"],
            "aliases": [],
        }
        for name in ("one", "two")
    ]
    claim = {
        "id": "claim:one",
        "entity": 0,
        "field": "manufacturer",
        "evidence_id": "page-one",
        "locator": {"kind": "fact", "index": 0},
        "raw": {"name": "Manufacturer", "raw": "A & B"},
        "status": "known",
        "value": {"text": "A & B"},
    }
    resolved = {
        **claim,
        "entity_id": "sample:one",
        "evidence_url": "https://sample.test/one",
    }
    relation: dict = {
        "id": "relation:one",
        "source": 0,
        "target": 1,
        "type": "related_system",
        "basis": "reviewed_source_evidence",
        "rationale": "Both pages document this connection.",
        "evidence": [
            {"entity": i, "evidence_id": f"page-{name}", "quote": "Original quote."}
            for i, name in enumerate(("one", "two"))
        ],
    }
    linked = {
        **relation,
        "source_id": "sample:one",
        "target_id": "sample:two",
        "evidence": [
            {
                **reference,
                "entity_id": index[reference["entity"]]["id"],
                "evidence_url": index[reference["entity"]]["url"],
            }
            for reference in relation["evidence"]
        ],
    }
    fields = [
        {
            "field": field["name"],
            "kind": field["kind"],
            "unit": field["unit"],
            "entities": [
                {
                    "entity_id": entity["id"],
                    "status": "claims" if i == 0 and j == 0 else "unknown",
                    "claims": [resolved] if i == 0 and j == 0 else [],
                }
                for i, entity in enumerate(index)
            ],
        }
        for j, field in enumerate(manifest["research"]["fields"])
    ]
    predicate = "manufacturer = A & B"
    responses = {
        ("list", "--limit", "1"): {"total": 2, "results": index[:1]},
        (
            "list",
            "--limit",
            "100",
            "--source",
            "sample",
            "--kind",
            "radar",
            "--category",
            "radars",
        ): {"total": 2, "results": index},
        ("facts", "sample:one"): {"entity_id": "sample:one", "claims": [resolved]},
        ("facts", "sample:two"): {"entity_id": "sample:two", "claims": []},
        ("relationships", "--type", "related_system", "sample:one"): {
            "entity_id": "sample:one",
            "relationships": [linked],
        },
        ("compare", "sample:one", "sample:two"): {
            "entity_ids": ["sample:one", "sample:two"],
            "fields": fields,
        },
        ("list", "--where", predicate, "--limit", "100"): {
            "total": 1,
            "results": index[:1],
        },
        ("list", "--where", predicate, "--where", "manufacturer != A & B"): {
            "total": 0,
            "results": [],
        },
        ("search", "--mode", "vector", "--where", predicate, "radar"): {
            "results": index[:1]
        },
        ("search", "--where", predicate, "radar"): {"results": index[:1]},
        ("similar", "--where", predicate, "sample:one"): {"results": []},
    }
    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr("index.json", json.dumps(index))
        archive.writestr("research/claims.json", json.dumps([claim]))
        archive.writestr("research/relations.json", json.dumps([relation]))
        for entity in index:
            name = entity["id"].split(":")[1]
            archive.writestr(
                f"entities/sample/{name}.json",
                json.dumps(
                    {"evidence": [{"id": f"page-{name}", "url": entity["url"]}]}
                ),
            )
    return body.getvalue(), manifest, info, responses


def test_research_acceptance_checks_archived_claims_links_and_filtered_commands() -> (
    None
):
    body, manifest, info, responses = research_acceptance_fixture()
    calls = []

    def run(*args: str) -> bytes:
        calls.append(args)
        return json.dumps(
            {"dataset_id": manifest["dataset_id"], **responses[args]}
        ).encode()

    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        accept_research(run, archive, manifest, info)
    assert set(calls) == set(responses)


@pytest.mark.parametrize("damage", ["raw", "relation", "unknown", "filter", "total"])
def test_research_acceptance_rejects_lost_evidence_and_filter_leaks(
    damage: str,
) -> None:
    body, manifest, info, responses = research_acceptance_fixture()
    responses = deepcopy(responses)
    if damage == "raw":
        responses[("facts", "sample:one")]["claims"][0]["raw"] = {}
    elif damage == "relation":
        responses[("relationships", "--type", "related_system", "sample:one")][
            "relationships"
        ][0]["evidence"][1]["evidence_url"] = "https://unrelated.test/"
    elif damage == "unknown":
        responses[("compare", "sample:one", "sample:two")]["fields"][1]["entities"][1][
            "status"
        ] = "known"
    elif damage == "filter":
        responses[("search", "--where", "manufacturer = A & B", "radar")]["results"] = [
            {"id": "sample:two"}
        ]
    else:
        responses[("list", "--limit", "1")]["total"] = 1

    def run(*args: str) -> bytes:
        return json.dumps(
            {"dataset_id": manifest["dataset_id"], **responses[args]}
        ).encode()

    with zipfile.ZipFile(io.BytesIO(body)) as archive, pytest.raises(AssertionError):
        accept_research(run, archive, manifest, info)


def test_research_acceptance_does_not_run_new_commands_for_legacy_bundles() -> None:
    def run(*args: str) -> bytes:
        raise AssertionError(f"Legacy bundle executed {args}")

    body = io.BytesIO()
    with zipfile.ZipFile(body, "w") as archive:
        accept_research(run, archive, {}, {})
