import copy
import json
import zipfile
from pathlib import Path

import pytest

from pipelines import research
from pipelines.distribution import canonical, sha256, write_bundle
from pipelines.research import build_research, normalize
from pipelines.research_distribution import (
    CLAIMS_MEMBER,
    RELATIONS_MEMBER,
    build_research_members,
    validate_research_bundle,
)


def fact(raw: str, name: str = "Range", **extra: object) -> dict:
    return {
        "name": name,
        "raw": raw,
        "evidence": "https://source.test/one",
        "values": [],
        "unit": None,
        "qualifier": None,
        **extra,
    }


@pytest.mark.parametrize(
    ("field", "raw", "expected"),
    [
        ("range", "600 kilometer | ", (600000, 600000, "m")),
        ("frequency", "2.9 … 3.3 GHz", (2.9e9, 3.3e9, "Hz")),
        ("power", "53.5 kW", (53500, 53500, "W")),
        ("power", "100 mW", (0.1, 0.1, "W")),
        ("power", "100 MW", (1e8, 1e8, "W")),
        ("frequency", "1 mHz", (0.001, 0.001, "Hz")),
        ("frequency", "1 MHz", (1e6, 1e6, "Hz")),
        ("mass", "50,000 kilogram", (50000, 50000, "kg")),
        ("range", "1e3 km", (1e6, 1e6, "m")),
        ("length", ".5 m", (0.5, 0.5, "m")),
        ("length", "12 inches", (0.3048, 0.3048, "m")),
        ("speed", "108 km/h", (30, 30, "m/s")),
        ("crew", "0", (0, 0, "count")),
        ("range", "> 100 km", (100000, None, "m")),
        ("range", "Up to 300 km", (None, 300000, "m")),
    ],
)
def test_conservative_normalized_quantities(
    field: str, raw: str, expected: tuple
) -> None:
    status, value = normalize(field, fact(raw))
    assert status == "known" and value
    number = value["number"]
    assert (
        number["min"] == pytest.approx(expected[0])
        if expected[0] is not None
        else number["min"] is None
    )
    assert (
        number["max"] == pytest.approx(expected[1])
        if expected[1] is not None
        else number["max"] is None
    )
    assert number["unit"] == expected[2]
    assert number["min_inclusive"] == (
        expected[0] is not None and not raw.startswith("> ")
    )
    assert number["max_inclusive"] == (expected[1] is not None)


@pytest.mark.parametrize(
    "raw",
    [
        "240 NM (≙ 450 km)",
        "600 km | favorable conditions",
        "5 or 10 km",
        "2 km/3 km",
        "1,5 km",
        "1,23,456 km",
        "-12 km",
        "300-100 km",
        "NaN km",
        "1e999 km",
        "400",
        "400 kg",
        "Mach 2",
        "92N6: 300 km; 92N6A: 480 km",
    ],
)
def test_ambiguous_numeric_claims_are_retained_without_invented_values(
    raw: str,
) -> None:
    original = fact(raw, values=[999999], unit=None)
    before = copy.deepcopy(original)
    assert normalize("range", original) == ("unparsed", None)
    assert original == before


@pytest.mark.parametrize("raw", ["?", "Unknown", "N/A", "—", "? | "])
def test_unknown_never_becomes_zero(raw: str) -> None:
    assert normalize("quantity", fact(raw)) == ("unknown", None)


def test_approximation_conditions_and_unit_hints_remain_explicit() -> None:
    status, value = normalize("range", fact("about 10 km"))
    assert status == "approximate" and value is not None
    assert value["number"]["min"] == 10000
    assert normalize("range", fact("10 km", qualifier="with external tanks")) == (
        "unparsed",
        None,
    )
    _, hinted = normalize("range", fact("10", unit="km"))
    assert hinted is not None and hinted["number"]["min"] == 10000
    assert normalize("range", fact("10 km", unit="m")) == ("unparsed", None)
    assert normalize("crew", fact("2.5")) == ("unparsed", None)
    assert normalize("length", fact("2 Mm")) == ("unparsed", None)
    assert normalize("power", fact("2 mw")) == ("unparsed", None)
    assert normalize("frequency", fact("2 MHZ")) == ("unparsed", None)


@pytest.mark.parametrize(
    ("raw", "start", "end", "precision"),
    [
        ("2000", "2000-01-01", "2000-12-31", "year"),
        ("2000-02", "2000-02-01", "2000-02-29", "month"),
        ("April 28, 2007", "2007-04-28", "2007-04-28", "day"),
        ("2026-09-21T10:40:00Z", "2026-09-21", "2026-09-21", "day"),
    ],
)
def test_date_precision_is_not_false_day_precision(
    raw: str, start: str, end: str, precision: str
) -> None:
    assert normalize("service_entry", fact(raw)) == (
        "known",
        {"date": {"min": start, "max": end, "precision": precision}},
    )


@pytest.mark.parametrize(
    "raw", ["0000", "2000-13", "2001-02-29", "2000-2005", "04/05/2006", "tomorrow"]
)
def test_uncertain_or_invalid_dates_do_not_compare(raw: str) -> None:
    assert normalize("service_entry", fact(raw)) == ("unparsed", None)


@pytest.fixture
def records(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows: list[dict] = [
        {
            "id": "alpha:one",
            "source": "alpha",
            "facts": [
                fact("100 km"),
                fact("North & South, Inc.", "Maker"),
                fact("2030", "IOC"),
            ],
            "evidence": [
                {
                    "id": "first",
                    "url": "https://source.test/one",
                    "markdown": "Model Alpha is the first system.",
                    "retrieved_at": "2026-09-21T10:40:00Z",
                }
            ],
        },
        {
            "id": "beta:two",
            "source": "beta",
            "facts": [fact("300 km", evidence="https://source.test/two")],
            "evidence": [
                {
                    "id": "second",
                    "url": "https://source.test/two",
                    "markdown": "Model Beta uses the first system.",
                    "retrieved_at": "",
                }
            ],
        },
    ]
    relation = {
        "source_id": "beta:two",
        "target_id": "alpha:one",
        "type": "related_system",
        "basis": "reviewed_source_evidence",
        "rationale": "Explicit source relationship, not identity equivalence.",
        "evidence": [
            {
                "entity_id": row["id"],
                "evidence_id": row["evidence"][0]["id"],
                "quote": row["evidence"][0]["markdown"],
            }
            for row in rows
        ],
    }
    rules = {
        "fields": {"Range": "range", "Maker": "manufacturer", "IOC": "service_entry"},
        "relations": [],
    }
    monkeypatch.setattr(
        research,
        "source_rules",
        lambda source: {**rules, "relations": [relation] if source == "beta" else []},
    )
    return rows


def test_claims_and_relationships_preserve_raw_evidence_without_merging(
    records: list[dict],
) -> None:
    before = copy.deepcopy(records)
    claims, relations = build_research(records)
    assert records == before
    assert len(claims) == 5 and len(relations) == 1
    assert [row["id"] for row in claims] == sorted(row["id"] for row in claims)
    assert relations[0]["source"] == 0 and relations[0]["target"] == 1
    assert relations[0]["type"] == "related_system"
    original = next(row for row in claims if row["field"] == "manufacturer")
    assert original["raw"] == records[0]["facts"][1]
    assert original["value"] == {"text": "North & South, Inc."}
    capture = next(row for row in claims if row["field"] == "captured_date")
    assert capture["locator"] == {"kind": "captured_at", "index": 0}
    assert capture["evidence_id"] == "first"
    assert (
        next(row for row in claims if row["field"] == "service_entry")["raw"]["raw"]
        == "2030"
    )
    subset, links = build_research(records[:1])
    assert len(subset) == 4 and links == []
    reordered, _ = build_research(list(reversed(records)))
    assert {row["id"] for row in reordered} == {row["id"] for row in claims}


def test_relationships_must_quote_each_retained_endpoint(records: list[dict]) -> None:
    records[1]["evidence"][0]["markdown"] = "The source changed."
    with pytest.raises(ValueError, match="quote"):
        build_research(records)


def packed(records: list[dict], tmp_path: Path) -> tuple[dict, dict[str, bytes]]:
    members, metadata = build_research_members(records)
    members["index.json"] = canonical(
        [{key: row[key] for key in ("id", "source")} for row in records]
    )
    members.update(
        {
            "entities/" + row["id"].replace(":", "/") + ".json": canonical(row)
            for row in records
        }
    )
    manifest = {
        "format_version": 2,
        "research": metadata,
        "files": {name: sha256(raw) for name, raw in members.items()},
    }
    return manifest, members


def validate(
    tmp_path: Path,
    manifest: dict,
    members: dict[str, bytes],
    records: list[dict] | None = None,
) -> dict | None:
    write_bundle(tmp_path / "research.zip", members)
    with zipfile.ZipFile(tmp_path / "research.zip") as archive:
        return validate_research_bundle(archive, manifest, records)


def test_bundled_claims_reproduce_from_original_sources(
    records: list[dict], tmp_path: Path
) -> None:
    manifest, members = packed(records, tmp_path)
    assert validate(tmp_path, manifest, members) == manifest["research"]
    assert validate(tmp_path, manifest, members, records) == manifest["research"]
    rows = json.loads(members[CLAIMS_MEMBER])
    target = next(row for row in rows if row["field"] == "range")
    target["value"]["number"]["min"] = 99999
    members[CLAIMS_MEMBER] = canonical(rows)
    manifest["files"][CLAIMS_MEMBER] = sha256(members[CLAIMS_MEMBER])
    with pytest.raises(ValueError, match="reproduce"):
        validate(tmp_path, manifest, members)


def test_unknown_research_extensions_and_corrupt_unused_links_fail(
    records: list[dict], tmp_path: Path
) -> None:
    manifest, members = packed(records, tmp_path)
    rows = json.loads(members[RELATIONS_MEMBER])
    rows[0]["evidence"][0]["quote"] = "fabricated"
    members[RELATIONS_MEMBER] = canonical(rows)
    manifest["files"][RELATIONS_MEMBER] = sha256(members[RELATIONS_MEMBER])
    with pytest.raises(ValueError, match="reproduce"):
        validate(tmp_path, manifest, members)
    manifest.pop("research")
    with pytest.raises(ValueError, match="without manifest"):
        validate(tmp_path, manifest, members)
    assert validate(tmp_path, {"files": {}}, {}) is None


def test_all_source_configs_use_the_shared_catalog() -> None:
    from pipelines.registry import source_names

    for source in source_names():
        rules = research.source_rules(source)
        assert isinstance(rules["relations"], list)
