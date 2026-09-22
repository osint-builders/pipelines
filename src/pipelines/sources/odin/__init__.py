import hashlib
import json
import re
from collections.abc import Iterable
from typing import TYPE_CHECKING

from bs4 import BeautifulSoup
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence
from pipelines.sources.odin.content import (
    aliases,
    record_facts,
    render,
    scalar,
    sections,
    terms,
)

if TYPE_CHECKING:
    from pipelines.media import MediaCandidate

ORIGIN = "https://odin.t2com.army.mil"
API = ORIGIN + "/dotcms/api/content/_search"
SUBNAV = ORIGIN + "/dotcms/api/subnav/weg"
PAGE_SIZE = 100
HOST = "8a7d5e23-da1e-420a-b4f0-471e7da8ea2d"
QUERY = "+contentType:WegCard +live:true +deleted:false +conhost:" + HOST
SORT = "identifier asc"


def page_url(offset: int) -> str:
    if type(offset) is not int or offset < 0 or offset % PAGE_SIZE:
        raise ValueError("Invalid ODIN page offset")
    return f"{API}?limit={PAGE_SIZE}&offset={offset}"


def page_offset(url: str) -> int:
    match = re.fullmatch(
        re.escape(API) + rf"\?limit={PAGE_SIZE}&offset=(0|[1-9][0-9]*)", url
    )
    if match is None or int(match[1]) % PAGE_SIZE:
        raise ValueError("Outside ODIN catalog scope")
    return int(match[1])


def category_tree(body: bytes) -> dict:
    try:
        data = json.loads(body)["content"]
        domains = data["children"]["domain"]["children"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Missing ODIN category hierarchy") from exc
    if not isinstance(domains, dict) or not domains:
        raise ValueError("Empty ODIN category hierarchy")

    def validate(nodes: dict, depth: int = 0) -> None:
        if depth > 30:
            raise ValueError("Invalid ODIN category depth")
        for key, node in nodes.items():
            if (
                not isinstance(node, dict)
                or node.get("key") != key
                or not isinstance(node.get("name"), str)
                or not node["name"].strip()
                or not isinstance(node.get("children"), dict)
            ):
                raise ValueError("Invalid ODIN category node")
            validate(node["children"], depth + 1)

    validate(domains)
    return data


def validated_record(record: dict) -> None:
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("identifier"), str)
        or not re.fullmatch(r"[0-9a-f]{32}", record["identifier"])
    ):
        raise ValueError("Invalid ODIN record identifier")
    if (
        record.get("contentType") != "WegCard"
        or record.get("live") is not True
        or record.get("archived") is not False
        or record.get("host") != HOST
    ):
        raise ValueError("Unexpected ODIN record content scope")
    if not scalar(record.get("name") or record.get("title"), "title"):
        raise ValueError("Missing ODIN record title")
    for field in ("domain", "origin", "proliferation"):
        terms(record, field)
    sections(record)


def payload(body: bytes) -> tuple[int, list[dict]]:
    try:
        data = json.loads(body)
        if not isinstance(data, dict):
            raise ValueError("Invalid ODIN search envelope")
        if data.get("errors"):
            raise ValueError("ODIN search returned errors")
        entity = data["entity"]
        total = entity["resultsSize"]
        records = entity["jsonObjectView"]["contentlets"]
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError("Missing ODIN search response") from exc
    if type(total) is not int or total <= 0 or not isinstance(records, list):
        raise ValueError("Invalid ODIN result count")
    for record in records:
        validated_record(record)
    identifiers = [record["identifier"] for record in records]
    if identifiers != sorted(set(identifiers)):
        raise ValueError("ODIN page IDs are repeated or not sorted")
    return total, records


def page_records(url: str, body: bytes) -> tuple[int, list[dict]]:
    offset = page_offset(url)
    total, records = payload(body)
    if offset >= total or len(records) != min(PAGE_SIZE, total - offset):
        raise ValueError("ODIN page size does not match its result count")
    return total, records


def validate_pages(pages: Iterable[tuple[str, bytes]]) -> dict[int, list[dict]]:
    result: dict[int, list[dict]] = {}
    expected = None
    for url, body in pages:
        if url == SUBNAV:
            category_tree(body)
            continue
        offset = page_offset(url)
        total, records = page_records(url, body)
        if expected is not None and total != expected:
            raise ValueError("ODIN result count changed during capture")
        if offset in result:
            raise ValueError("Repeated ODIN archive page")
        result[offset] = records
        expected = total
    if expected is None or set(result) != set(range(0, expected, PAGE_SIZE)):
        raise ValueError("Incomplete ODIN catalog pages")
    identifiers = [
        record["identifier"] for offset in sorted(result) for record in result[offset]
    ]
    if len(identifiers) != expected or identifiers != sorted(set(identifiers)):
        raise ValueError("ODIN catalog IDs overlap or are not globally sorted")
    return result


def canonical_url(record: dict) -> str:
    validated_record(record)
    return ORIGIN + "/WEG/Asset/" + record["identifier"]


def categories(record: dict) -> list[str]:
    return terms(record, "domain")


def entity_kind(record: dict) -> EntityKind:
    labels = {label.lower() for label in categories(record)}
    if "naval watercraft" in labels or any(
        label.endswith(("ships", "boats", "submarines")) or "surface vehicles" in label
        for label in labels
    ):
        return EntityKind.VESSEL
    if any(
        "missile" in label or "landmine" in label or "grenade" in label
        for label in labels
    ) or labels & {"infantry weapons", "artillery", "anti-aircraft guns", "mortars"}:
        return EntityKind.WEAPON
    if "radar systems" in labels or any(label.endswith("radars") for label in labels):
        return EntityKind.RADAR
    if "aircraft armament" not in labels and (
        "aircraft" in labels
        or any("helicopter" in label or "uavs" in label for label in labels)
    ):
        return EntityKind.AIRCRAFT
    if labels & {
        "infantry vehicles",
        "tanks",
        "heavy armored vehicles",
        "light armored vehicles",
    } or any("truck" in label or "personnel carrier" in label for label in labels):
        return EntityKind.VEHICLE
    if any(label.endswith("sensors") for label in labels):
        return EntityKind.SENSOR
    return EntityKind.EQUIPMENT


class Odin:
    id = "odin"
    version = "1"
    seeds: tuple[str, ...] = (page_url(0), SUBNAV)
    minimum_entities = 4000
    media_origins = (ORIGIN,)
    media_workers = 8
    media_request_interval = 0.1

    def __init__(self) -> None:
        self.prepared_pages: dict[str, str] = {}

    def normalize(self, url: str) -> str | None:
        if url == SUBNAV:
            return url
        try:
            return page_url(page_offset(url))
        except ValueError:
            return None

    def request_body(self, url: str) -> bytes | None:
        if url == SUBNAV:
            return None
        return json.dumps(
            {
                "limit": PAGE_SIZE,
                "offset": page_offset(url),
                "query": QUERY,
                "sort": SORT,
            },
            separators=(",", ":"),
        ).encode()

    def request_headers(self, url: str) -> dict[str, str]:
        from pipelines.sources.odin.media import media_url

        if media_url(url) == url:
            return {"Accept": "image/*", "Referer": ORIGIN + "/WEG/List"}
        if self.normalize(url) != url:
            raise ValueError("Outside ODIN request scope")
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": ORIGIN + "/WEG/List",
        }

    def discover(self, url: str, body: bytes) -> list[str]:
        if url == SUBNAV:
            category_tree(body)
            return []
        total, _ = page_records(url, body)
        return (
            [page_url(offset) for offset in range(PAGE_SIZE, total, PAGE_SIZE)]
            if page_offset(url) == 0
            else []
        )

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        saved = list(pages)
        if sum(url == SUBNAV for url, _ in saved) != 1:
            raise ValueError("ODIN extraction requires its category hierarchy")
        validate_pages(saved)
        self.prepared_pages = {
            url: hashlib.sha256(body).hexdigest() for url, body in saved
        }

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if self.prepared_pages.get(url) != hashlib.sha256(body).hexdigest():
            raise ValueError("ODIN extraction requires a complete unchanged catalog")
        if url == SUBNAV:
            return []
        _, records = page_records(url, body)
        entities = []
        for record in records:
            canonical = canonical_url(record)
            title = scalar(record.get("name") or record.get("title"), "title")
            html = render(record)
            focused = BeautifulSoup(html, "html.parser")
            markdown = markdownify(str(focused.main), heading_style="ATX").strip()
            for gallery in focused.select("section.source-images"):
                gallery.decompose()
            search_text = markdownify(str(focused.main), heading_style="ATX").strip()
            entity = Entity(
                key=record["identifier"],
                title=title,
                kind=entity_kind(record),
                categories=categories(record),
                aliases=aliases(record),
                facts=record_facts(record, canonical),
                url=canonical,
                evidence=[
                    Evidence(
                        url=url,
                        title=title,
                        markdown=markdown,
                        canonical_url=canonical,
                        rendered_html=html,
                        search_text=search_text,
                        record_id=record["identifier"],
                        records=[record],
                    )
                ],
            )
            entity.validate()
            entities.append(entity)
        return entities

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> Iterable["MediaCandidate"]:
        from pipelines.sources.odin.media import candidates

        if self.normalize(url) != url:
            raise ValueError("Outside ODIN media discovery scope")
        if url == SUBNAV:
            return []
        return candidates(url, body, entities)

    def audit(
        self, entities: list[dict], responses: dict[str, bytes], html: dict[str, bytes]
    ) -> dict:
        from pipelines.sources.odin.audit import audit_snapshot

        return audit_snapshot(self, entities, responses, html)
