"""Normalize retained source claims without merging source identities."""

import calendar
import json
import math
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path

from pipelines.distribution import canonical, sha256

VERSION = "entity-research-v2"
RULES_ROOT = Path(__file__).with_name("sources")
TEXT_FIELDS = (
    "manufacturer contractor origin_country designer_country operator_country "
    "site_country development_status modulation reception_mode signal_location signal_status"
).split()
DATE_FIELDS = (
    "service_entry first_flight launch_date retired publication_date updated_date captured_date"
).split()
NUMBER_FIELDS = {
    **dict.fromkeys(
        "range detection_range ferry_range ceiling length height width wavelength".split(),
        "m",
    ),
    "mass": "kg",
    "speed": "m/s",
    "frequency": "Hz",
    "pulse_repetition_frequency": "Hz",
    "frequency_range": "Hz",
    "bandwidth": "Hz",
    "power": "W",
    "crew": "count",
    "quantity": "count",
}
FIELDS = sorted(
    [
        *({"name": name, "kind": "text", "unit": ""} for name in TEXT_FIELDS),
        *({"name": name, "kind": "date", "unit": ""} for name in DATE_FIELDS),
        *(
            {"name": name, "kind": "number", "unit": unit}
            for name, unit in NUMBER_FIELDS.items()
        ),
    ],
    key=lambda field: field["name"],
)
FIELD_NAMES = {field["name"] for field in FIELDS}
SIGNAL_FIELDS = {
    "frequency_range",
    "bandwidth",
    "modulation",
    "reception_mode",
    "signal_location",
    "signal_status",
}
LEGACY_FIELDS = [field for field in FIELDS if field["name"] not in SIGNAL_FIELDS]
FIELD_CATALOGS = {"entity-research-v1": LEGACY_FIELDS, VERSION: FIELDS}
RELATION_TYPES = {
    "equivalent",
    "related_system",
    "variant_of",
    "family_member_of",
    "component_of",
}
SYMMETRIC = {"equivalent", "related_system"}
UNKNOWN = {
    "",
    "?",
    "-",
    "—",
    "–",
    "n/a",
    "na",
    "unknown",
    "not known",
    "not available",
    "no data",
}
NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?|\.\d+(?:[eE][+-]?\d+)?"
UNITS: dict[str, tuple[str, float]] = {}
for _unit, _factor, _aliases in [
    ("m", 1, "m meter meters metre metres"),
    ("m", 1000, "km kilometer kilometers kilometre kilometres"),
    ("m", 0.01, "cm centimeter centimeters centimetre centimetres"),
    ("m", 0.001, "mm millimeter millimeters millimetre millimetres"),
    ("m", 0.3048, "ft foot feet"),
    ("m", 0.0254, "in inch inches"),
    ("m", 1609.344, "mi mile miles"),
    ("m", 1852, "nmi"),
    ("kg", 1, "kg kilogram kilograms"),
    ("kg", 0.001, "g gram grams"),
    ("kg", 1000, "t tonne tonnes"),
    ("kg", 0.45359237, "lb lbs pound pounds"),
    ("m/s", 1, "m/s"),
    ("m/s", 1 / 3.6, "km/h kph"),
    ("m/s", 0.44704, "mph"),
    ("m/s", 1852 / 3600, "kn knot knots"),
    ("Hz", 1, "hz"),
    ("Hz", 1000, "khz"),
    ("Hz", 1e6, "mhz"),
    ("Hz", 1e9, "ghz"),
    ("W", 1, "w"),
    ("W", 1000, "kw"),
    ("W", 1e6, "mw"),
    ("count", 1, "count"),
]:
    for _alias in _aliases.split():
        UNITS[_alias] = (_unit, _factor)
UNITS["metric ton"] = UNITS["metric tons"] = ("kg", 1000)
SYMBOLS = {
    symbol: UNITS[symbol.lower()]
    for symbol in "m km cm mm kg g t m/s km/h Hz kHz MHz GHz W kW MW".split()
}
SYMBOLS.update({"mW": ("W", 0.001), "mHz": ("Hz", 0.001)})
RESERVED_SYMBOLS = {symbol.lower() for symbol in SYMBOLS}


def text_key(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).lower().split())


def unit_spec(value: str) -> tuple[str, float] | None:
    symbol = " ".join(unicodedata.normalize("NFKC", value).split())
    if symbol in SYMBOLS:
        return SYMBOLS[symbol]
    key = text_key(value)
    return None if key in RESERVED_SYMBOLS else UNITS.get(key)


def source_rules(source: str) -> dict:
    if not re.fullmatch(r"[a-z0-9_-]+", source):
        raise ValueError("Invalid research source")
    path = RULES_ROOT / source / "research.json"
    rules = (
        json.loads(path.read_bytes())
        if path.is_file()
        else {"fields": {}, "relations": []}
    )
    if (
        not isinstance(rules, dict)
        or set(rules) != {"fields", "relations"}
        or not isinstance(rules["fields"], dict)
        or not isinstance(rules["relations"], list)
        or any(
            not isinstance(key, str) or value not in FIELD_NAMES
            for key, value in rules["fields"].items()
        )
    ):
        raise ValueError("Invalid source research rules")
    labels = [text_key(label) for label in rules["fields"]]
    if len(set(labels)) != len(labels) or any(not label for label in labels):
        raise ValueError("Ambiguous source field mapping")
    return rules


def date_value(raw: str) -> dict | None:
    value = raw.strip()
    try:
        if re.fullmatch(r"\d{4}", value):
            year = int(value)
            return {
                "min": date(year, 1, 1).isoformat(),
                "max": date(year, 12, 31).isoformat(),
                "precision": "year",
            }
        if re.fullmatch(r"\d{4}-\d{2}", value):
            year, month = map(int, value.split("-"))
            return {
                "min": date(year, month, 1).isoformat(),
                "max": date(
                    year, month, calendar.monthrange(year, month)[1]
                ).isoformat(),
                "precision": "month",
            }
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            day = date.fromisoformat(value)
        elif re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", value):
            day = datetime.fromisoformat(value).date()
        else:
            months = {
                name.lower(): position
                for position, name in enumerate(calendar.month_name)
                if name
            }
            match = re.fullmatch(r"([A-Za-z]+) (\d{1,2}), (\d{4})", value)
            if not match or match[1].lower() not in months:
                return None
            day = date(int(match[3]), months[match[1].lower()], int(match[2]))
        return {"min": day.isoformat(), "max": day.isoformat(), "precision": "day"}
    except (ValueError, OverflowError):
        return None


def number_value(field: str, raw: str, unit_hint: str | None) -> dict | None:
    value = raw.replace("−", "-").replace("≤", "<=").replace("≥", ">=").strip()
    if field in {"frequency_range", "bandwidth"}:
        # SigIDWiki repeats units at both endpoints, sometimes with different
        # scales. Preserve the source string and convert each endpoint separately.
        value = value.replace("—", "–")
        interval = re.fullmatch(
            rf"({NUMBER})\s*([A-Za-z]+)\s*[-–…]\s*({NUMBER})\s*([A-Za-z]+)", value
        )
        if interval:
            lower = number_value(field, interval[1] + " " + interval[2], unit_hint)
            upper = number_value(field, interval[3] + " " + interval[4], unit_hint)
            if lower is None or upper is None or lower["min"] > upper["max"]:
                return None
            return {**lower, "max": upper["max"]}
    for phrase, operator in (
        ("up to ", "<="),
        ("at least ", ">="),
        ("less than ", "<"),
        ("more than ", ">"),
    ):
        if value.lower().startswith(phrase):
            value = operator + value[len(phrase) :]
            break
    match = re.fullmatch(
        rf"\s*(<=|>=|<|>)?\s*({NUMBER})\s*(?:([-–…]|\.{{3}}|to)\s*({NUMBER})\s*)?([^0-9]*)",
        value,
    )
    if not match or (match[1] and match[4]):
        return None
    unit = match[5] or unit_hint or ("count" if NUMBER_FIELDS[field] == "count" else "")
    spec = unit_spec(unit)
    if spec is None or spec[0] != NUMBER_FIELDS[field]:
        return None
    if unit_hint and match[5].strip():
        hint = unit_spec(unit_hint)
        if hint is None or hint != spec:
            return None
    try:
        low = float(match[2].replace(",", "")) * spec[1]
        high = float(match[4].replace(",", "")) * spec[1] if match[4] else low
    except (ValueError, OverflowError):
        return None
    if not all(math.isfinite(n) and n >= 0 for n in (low, high)) or low > high:
        return None
    if spec[0] == "count" and (not low.is_integer() or not high.is_integer()):
        return None
    operator = match[1]
    return {
        "min": None if operator in {"<", "<="} else low,
        "max": None if operator in {">", ">="} else high,
        "min_inclusive": operator not in {"<", "<=", ">"},
        "max_inclusive": operator not in {">", ">=", "<"},
        "unit": spec[0],
    }


def normalize(field: str, fact: dict) -> tuple[str, dict | None]:
    if field not in FIELD_NAMES:
        raise ValueError("Unknown research field")
    raw = " ".join(unicodedata.normalize("NFKC", fact["raw"]).split())
    # A trailing empty table cell has no qualifier; populated notes remain unresolved.
    if "|" in raw:
        cells = raw.split("|")
        if any(cell.strip() for cell in cells[1:]):
            return "unparsed", None
        raw = cells[0].strip()
    if text_key(raw) in UNKNOWN:
        return "unknown", None
    approximate = re.match(
        r"^(?:~|≈|about\s+|approx(?:imately|\.)?\s+|ca\.\s+)", raw, re.I
    )
    qualifier = text_key(fact.get("qualifier") or "")
    status = (
        "approximate"
        if approximate or re.search(r"approx|estimate|about|~|≈", qualifier)
        else "known"
    )
    if approximate:
        raw = raw[approximate.end() :].strip()
    value: dict | None
    if field in TEXT_FIELDS:
        value = {"text": raw}
    elif field in DATE_FIELDS:
        parsed_date = date_value(raw)
        value = {"date": parsed_date} if parsed_date else None
    else:
        if qualifier not in {"", "value", "range"} and status != "approximate":
            return "unparsed", None
        parsed_number = number_value(field, raw, fact.get("unit"))
        value = {"number": parsed_number} if parsed_number else None
    return (status if value is not None else "unparsed"), value


def retained_page(entity: dict, url: str) -> dict:
    base = url.split("#", 1)[0]
    pages = [
        page
        for page in entity["evidence"]
        if base in {page["url"], page.get("canonical_url")}
    ]
    if len(pages) != 1:
        raise ValueError("Claim must identify one retained evidence page")
    return pages[0]


def _claim(
    entity: dict, position: int, field: str, fact: dict, kind: str, index: int
) -> dict:
    required = {"name", "raw", "evidence", "values", "unit", "qualifier"}
    if set(fact) != required or not all(
        isinstance(fact[key], str) for key in ("name", "raw", "evidence")
    ):
        raise ValueError("Invalid original source fact")
    if len(fact["raw"].encode()) > 16384:
        raise ValueError("Source claim exceeds text limit")
    status, value = normalize(field, fact)
    row = {
        "entity": position,
        "field": field,
        "evidence_id": retained_page(entity, fact["evidence"])["id"],
        "locator": {"kind": kind, "index": index},
        "raw": fact,
        "status": status,
        "value": value,
    }
    return {
        "id": "claim:" + sha256(canonical({**row, "entity": entity["id"]}))[:24],
        **row,
    }


def build_research(entities: list[dict]) -> tuple[list[dict], list[dict]]:
    by_id = {entity["id"]: position for position, entity in enumerate(entities)}
    if len(by_id) != len(entities):
        raise ValueError("Duplicate research entity IDs")
    rules = {
        source: source_rules(source)
        for source in sorted({row["source"] for row in entities})
    }
    claims = []
    for position, entity in enumerate(entities):
        labels = {
            text_key(name): field
            for name, field in rules[entity["source"]]["fields"].items()
        }
        for index, fact in enumerate(entity.get("facts", [])):
            field = labels.get(text_key(fact["name"]))
            if field:
                claims.append(_claim(entity, position, field, fact, "fact", index))
        for index, page in enumerate(entity["evidence"]):
            if page.get("retrieved_at"):
                fact = {
                    "name": "Captured at",
                    "raw": page["retrieved_at"],
                    "evidence": page["url"],
                    "values": [],
                    "unit": None,
                    "qualifier": None,
                }
                # Record-rendered pages can share a URL; the evidence index is definitive.
                status, value = normalize("captured_date", fact)
                row = {
                    "entity": position,
                    "field": "captured_date",
                    "evidence_id": page["id"],
                    "locator": {"kind": "captured_at", "index": index},
                    "raw": fact,
                    "status": status,
                    "value": value,
                }
                claims.append(
                    {
                        "id": "claim:"
                        + sha256(canonical({**row, "entity": entity["id"]}))[:24],
                        **row,
                    }
                )
    relations = []
    pairs = set()
    for owner, rule in rules.items():
        for assertion in rule["relations"]:
            if (
                set(assertion)
                != {"source_id", "target_id", "type", "basis", "rationale", "evidence"}
                or assertion["type"] not in RELATION_TYPES
                or assertion["basis"] != "reviewed_source_evidence"
            ):
                raise ValueError("Invalid reviewed relationship")
            if not any(
                assertion[key].startswith(owner + ":")
                for key in ("source_id", "target_id")
            ):
                raise ValueError(
                    "Relationship assertion belongs with its source adapter"
                )
            if (
                assertion["source_id"] not in by_id
                or assertion["target_id"] not in by_id
            ):
                continue
            source, target = (
                by_id[assertion["source_id"]],
                by_id[assertion["target_id"]],
            )
            if (
                source == target
                or not isinstance(assertion["rationale"], str)
                or not assertion["rationale"].strip()
                or len(assertion["rationale"]) > 4096
            ):
                raise ValueError("Invalid reviewed relationship endpoints or rationale")
            if assertion["type"] in SYMMETRIC:
                source, target = sorted((source, target))
            pair = (source, target, assertion["type"])
            if pair in pairs:
                raise ValueError("Duplicate reviewed relationship")
            pairs.add(pair)
            refs = []
            for ref in assertion["evidence"]:
                if (
                    set(ref) != {"entity_id", "evidence_id", "quote"}
                    or ref["entity_id"] not in by_id
                ):
                    raise ValueError("Invalid relationship evidence")
                position = by_id[ref["entity_id"]]
                page = next(
                    (
                        p
                        for p in entities[position]["evidence"]
                        if p["id"] == ref["evidence_id"]
                    ),
                    None,
                )
                if (
                    position not in {source, target}
                    or page is None
                    or not isinstance(ref["quote"], str)
                    or not ref["quote"].strip()
                    or len(ref["quote"].encode()) > 16384
                    or ref["quote"] not in page["markdown"]
                ):
                    raise ValueError(
                        "Relationship quote must reference retained endpoint evidence"
                    )
                refs.append(
                    {
                        "entity": position,
                        "evidence_id": ref["evidence_id"],
                        "quote": ref["quote"],
                    }
                )
            if {ref["entity"] for ref in refs} != {source, target}:
                raise ValueError("Relationship requires evidence from both endpoints")
            refs.sort(key=lambda ref: (ref["entity"], ref["evidence_id"], ref["quote"]))
            if len({canonical(ref) for ref in refs}) != len(refs):
                raise ValueError("Duplicate relationship evidence")
            row = {
                "source": source,
                "target": target,
                "type": assertion["type"],
                "basis": "reviewed_source_evidence",
                "rationale": assertion["rationale"],
                "evidence": refs,
            }
            identity = {
                **row,
                "source": entities[source]["id"],
                "target": entities[target]["id"],
                "evidence": [
                    {**ref, "entity": entities[ref["entity"]]["id"]} for ref in refs
                ],
            }
            relations.append(
                {"id": "relation:" + sha256(canonical(identity))[:24], **row}
            )
    return sorted(claims, key=lambda row: row["id"]), sorted(
        relations, key=lambda row: row["id"]
    )
