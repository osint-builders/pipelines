import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.html import text

if TYPE_CHECKING:
    from pipelines.media import MediaCandidate

SEED = "https://cambridgepixel.com/resources/radar-database/"
HEADERS = ["Manufacturer", "Model", "Band", "Status", "Description", "URL"]


def identity(manufacturer: str, model: str) -> str:
    parts = [
        unicodedata.normalize("NFC", " ".join(value.split())).casefold()
        for value in (manufacturer, model)
    ]
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def catalog(body: bytes) -> tuple[dict, list[dict], str]:
    soup = BeautifulSoup(body, "html.parser")
    canonical = soup.select_one('link[rel="canonical"]')
    heading = soup.select_one("main h1")
    if (
        canonical is None
        or canonical.get("href") != SEED
        or heading is None
        or text(heading) != "Radar Database"
    ):
        raise ValueError("Cambridge Pixel catalog identity changed")
    schemas = [
        json.loads(node.get_text())
        for node in soup.select('script[type="application/ld+json"]')
    ]
    datasets = [
        s for s in schemas if isinstance(s, dict) and s.get("@type") == "Dataset"
    ]
    lists = [s for s in schemas if isinstance(s, dict) and s.get("@type") == "ItemList"]
    if len(datasets) != 1 or len(lists) != 1:
        raise ValueError("Missing or ambiguous Cambridge Pixel structured catalog")
    dataset, listing = datasets[0], lists[0]
    if dataset.get("license") or soup.select('a[rel="license"]'):
        raise ValueError("Cambridge Pixel licensing changed; review attribution")
    if (
        dataset.get("@id") != SEED + "#dataset"
        or listing.get("@id") != SEED + "#itemlist"
    ):
        raise ValueError("Unexpected Cambridge Pixel structured catalog identity")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dataset.get("dateModified", "")):
        raise ValueError("Missing Cambridge Pixel update date")
    tables = [
        table
        for table in soup.select("main table")
        if [text(h) for h in table.select("thead th")] == HEADERS
    ]
    if len(tables) != 1:
        raise ValueError("Missing or ambiguous radar model table")
    rows = tables[0].select("tbody > tr")
    entries = listing.get("itemListElement", [])
    count = listing.get("numberOfItems")
    counts = [
        int(match.group(1))
        for p in soup.select("main p")
        if (match := re.fullmatch(r"(\d+) radar models found\.", text(p)))
    ]
    if not rows or len(rows) != len(entries) or count != len(rows) or counts != [count]:
        raise ValueError("Incomplete Cambridge Pixel catalog counts")
    if sorted(e.get("position", 0) for e in entries) != list(range(1, count + 1)):
        raise ValueError("Invalid Cambridge Pixel catalog positions")
    records = {}
    for entry in entries:
        product = entry.get("item", {})
        manufacturer = product.get("manufacturer", {}).get("name", "")
        model = product.get("model", "")
        if (
            entry.get("@type") != "ListItem"
            or product.get("@type") != "ProductModel"
            or not isinstance(manufacturer, str)
            or not manufacturer.strip()
            or not isinstance(model, str)
            or not model.strip()
            or product.get("name") != manufacturer + " " + model
            or not isinstance(product.get("description"), str)
            or not product["description"].strip()
        ):
            raise ValueError("Invalid Cambridge Pixel product identity or description")
        key = identity(manufacturer, model)
        if key in records:
            raise ValueError("Duplicate Cambridge Pixel manufacturer/model identity")
        properties = product.get("additionalProperty", [])
        if (
            len(properties) != 3
            or {p.get("name") for p in properties} != {"Band", "Status", "Applications"}
            or any(
                not isinstance(p.get("value"), str) or not p["value"].strip()
                for p in properties
            )
        ):
            raise ValueError("Cambridge Pixel product properties changed")
        values = {p["name"]: p["value"] for p in properties}
        if values["Status"] not in {"Current", "Legacy"}:
            raise ValueError("Unknown Cambridge Pixel lifecycle status")
        if product.get("url") and urlsplit(product["url"]).scheme not in {
            "http",
            "https",
        }:
            raise ValueError("Invalid Cambridge Pixel manufacturer link")
        records[key] = product
    seen = set()
    for row in rows:
        for node in row.select(".show-mobile"):
            node.decompose()
        cells = row.find_all("td", recursive=False)
        if len(cells) != 6:
            raise ValueError("Cambridge Pixel table columns changed")
        manufacturer, model, band, status = [text(cell) for cell in cells[:4]]
        key = identity(manufacturer, model)
        product = records.get(key)
        if product is None or key in seen:
            raise ValueError("Cambridge Pixel table/JSON identities disagree")
        seen.add(key)
        applications = [text(node) for node in cells[4].select(".database-app span")]
        for node in cells[4].select(".database-app-list"):
            node.decompose()
        values = {p["name"]: p["value"] for p in product["additionalProperty"]}
        if (
            [manufacturer, model, band, status, text(cells[4])]
            != [
                product["manufacturer"]["name"],
                product["model"],
                values["Band"],
                values["Status"],
                product["description"],
            ]
            or ", ".join(applications) != values["Applications"]
            or [node["href"] for node in cells[5].select("a[href]")]
            != ([product["url"]] if product.get("url") else [])
        ):
            raise ValueError("Cambridge Pixel visible and structured record disagree")
    notices = [
        text(p)
        for p in soup.select("main p")
        if text(p).startswith("The radar data is mostly collated from manufacturers")
    ]
    footer = soup.select_one("footer")
    if (
        len(notices) != 1
        or footer is None
        or "Cambridge Pixel Ltd." not in text(footer)
    ):
        raise ValueError("Missing Cambridge Pixel attribution or data notice")
    return dataset, [records[key] for key in sorted(records)], notices[0]


class CambridgePixel:
    id = "cambridgepixel"
    version = "1"
    seeds: tuple[str, ...] = (SEED,)
    minimum_entities = 350
    media_origins = ("https://cambridgepixel.com",)
    media_workers = 2
    media_request_interval = 0.3

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> Iterable["MediaCandidate"]:
        from pipelines.sources.cambridgepixel.media import discover

        return discover(url, body, entities)

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.netloc != "cambridgepixel.com"
            or parts.query
            or parts.path.rstrip("/") != "/resources/radar-database"
        ):
            return None
        return SEED

    def discover(self, url: str, body: bytes) -> list[str]:
        if url != SEED:
            raise ValueError("Outside Cambridge Pixel source scope")
        catalog(body)
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if url != SEED:
            raise ValueError("Outside Cambridge Pixel source scope")
        dataset, records, notice = catalog(body)
        attribution = f"Copyright Cambridge Pixel Ltd. Source: {SEED}\nDatabase last updated: {dataset['dateModified']}.\n{notice}\nNo redistribution license is specified in the captured catalog. Manufacturer links are references; their contents are not part of this capture."
        result = []
        for product in records:
            manufacturer, model = product["manufacturer"]["name"], product["model"]
            key = identity(manufacturer, model)
            values = {p["name"]: p["value"] for p in product["additionalProperty"]}
            applications = values["Applications"].split(", ")
            # The source identifies VERA-NG as a passive ESM tracker, not a transmitter.
            kind = (
                EntityKind.SENSOR
                if values["Band"].startswith("Passive ESM")
                else EntityKind.RADAR
            )
            facts = [
                Fact("Manufacturer", manufacturer, SEED),
                Fact("Model", model, SEED),
            ]
            facts.extend(Fact(name, raw, SEED) for name, raw in values.items())
            facts.append(Fact("Database last updated", dataset["dateModified"], SEED))
            if product.get("url"):
                facts.append(Fact("Manufacturer reference", product["url"], SEED))
            fields = "\n".join(f"{f.name}: {f.raw}" for f in facts)
            record_json = json.dumps(
                product, indent=2, sort_keys=True, ensure_ascii=False
            )
            markdown = f"{product['description']}\n\n{fields}\n\n## Original ProductModel record\n\n```json\n{record_json}\n```"
            rendered = f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{escape(product["name"])}</title></head><body><h1>{escape(product["name"])}</h1><p>{escape(product["description"])}</p><pre>{escape(fields)}</pre><pre>{escape(attribution)}</pre><h2>Original ProductModel record</h2><pre>{escape(record_json)}</pre></body></html>\n'
            result.append(
                Entity(
                    key=key,
                    title=product["name"],
                    kind=kind,
                    aliases=[model],
                    categories=sorted(set(applications + [values["Status"]])),
                    facts=facts,
                    evidence=[
                        Evidence(
                            url=SEED,
                            title=product["name"],
                            markdown=markdown,
                            links=[product["url"]] if product.get("url") else [],
                            attribution=attribution,
                            rendered_html=rendered,
                            record_id=key,
                            records=[product],
                            search_text=f"{product['name']}\n{product['description']}\nBand: {values['Band']}\nLifecycle status: {values['Status']}\nApplications: {values['Applications']}",
                        )
                    ],
                )
            )
        return result
