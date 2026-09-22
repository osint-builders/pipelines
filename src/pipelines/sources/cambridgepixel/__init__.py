import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable
from html import escape
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.html import text

if TYPE_CHECKING:
    from pipelines.media import MediaCandidate

SEED = "https://cambridgepixel.com/resources/radar-database/"
HEADERS = ["Manufacturer", "Model", "Band", "Status", "Description", "URL"]
MANUFACTURER_ALIASES = {
    "Aerostar (was Raven Aerostar)": "Aerostar",
    "Anschütz": "Anschutz",
    "L3Harris Technologies (was ITT Gilfillan)": "L3Harris Technologies (formerly ITT Gilfillan)",
    "Numerica (now Anduril)": "Numerica",
    "Wärtsilä": "Wartsila",
}


def identity(manufacturer: str, model: str) -> str:
    manufacturer = MANUFACTURER_ALIASES.get(manufacturer, manufacturer)
    parts = [
        unicodedata.normalize("NFC", " ".join(value.split())).casefold()
        for value in (manufacturer, model)
    ]
    return hashlib.sha256(
        json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()[:24]


def reference_url(url: str) -> str:
    """Ignore referral tracking while retaining functional query parameters."""
    parts = urlsplit(url)
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if k not in {"utm_source", "utm_medium", "utm_campaign"}
    ]
    return urlunsplit(parts._replace(query=urlencode(query)))


def table_records(body: bytes) -> list[dict]:
    """Read every radar field from a full page or a pasted table fragment."""
    soup = BeautifulSoup(body, "html.parser")
    tables = [
        table
        for table in soup.select("table")
        if [text(h) for h in table.select("thead th")] == HEADERS
    ]
    if len(tables) != 1:
        raise ValueError("Missing or ambiguous radar model table")
    records = []
    seen = set()
    for row in tables[0].select("tbody > tr"):
        for node in row.select(".show-mobile"):
            node.decompose()
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(HEADERS):
            raise ValueError("Cambridge Pixel table columns changed")
        manufacturer, model, band, status = [
            " ".join(text(c).split()) for c in cells[:4]
        ]
        key = identity(manufacturer, model)
        if not manufacturer or not model or key in seen:
            raise ValueError("Duplicate or missing Cambridge Pixel table identity")
        seen.add(key)
        applications = [
            " ".join(text(n).split()) for n in cells[4].select(".database-app span")
        ]
        for node in cells[4].select(".database-app-list"):
            node.decompose()
        links = [str(n["href"]) for n in cells[5].select("a[href]")]
        if len(links) > 1 or any(
            urlsplit(u).scheme not in {"http", "https"} for u in links
        ):
            raise ValueError("Invalid Cambridge Pixel manufacturer link")
        records.append(
            {
                "id": key,
                "manufacturer": manufacturer,
                "model": model,
                "band": band,
                "status": status,
                "description": " ".join(text(cells[4]).split()),
                "applications": applications,
                "urls": links,
            }
        )
    if not records:
        raise ValueError("Empty Cambridge Pixel radar table")
    return records


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
    if (
        dataset.get("@id") != SEED + "#dataset"
        or listing.get("@id") != SEED + "#itemlist"
    ):
        raise ValueError("Unexpected Cambridge Pixel structured catalog identity")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", dataset.get("dateModified", "")):
        raise ValueError("Missing Cambridge Pixel update date")
    rows = table_records(body)
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
    for row in rows:
        product = records.get(row["id"])
        if product is None:
            raise ValueError("Cambridge Pixel table/JSON identities disagree")
        values = {p["name"]: p["value"] for p in product["additionalProperty"]}
        if (
            [
                row[name]
                for name in ("manufacturer", "model", "band", "status", "description")
            ]
            != [
                product["manufacturer"]["name"],
                product["model"],
                values["Band"],
                values["Status"],
                product["description"],
            ]
            or ", ".join(row["applications"]) != values["Applications"]
            or [reference_url(link) for link in row["urls"]]
            != ([reference_url(product["url"])] if product.get("url") else [])
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
    version = "2"
    minimum_entities = 350
    media_origins: tuple[str, ...] = ("https://cambridgepixel.com",)
    media_workers = 1
    media_request_interval = 0.3

    def __init__(self) -> None:
        from pipelines.sources.cambridgepixel.imagery import origins, reviews

        self.image_reviews = reviews()
        self.image_matches = [
            row for row in self.image_reviews if row["status"] == "matched"
        ]
        self.seeds = (SEED, *sorted({r["page_url"] for r in self.image_matches}))
        self.media_origins = (
            "https://cambridgepixel.com",
            *origins(self.image_matches),
        )
        self.catalog_entities: dict[str, Entity] = {}

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        bodies = dict(pages)
        self.catalog_entities = {e.key: e for e in self.extract(SEED, bodies[SEED], [])}

    def audit(
        self, entities: list[dict], responses: dict[str, bytes], html: dict[str, bytes]
    ) -> dict:
        from pipelines.sources.cambridgepixel.audit import audit_snapshot

        return audit_snapshot(self, entities, responses, html)

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> Iterable["MediaCandidate"]:
        from pipelines.sources.cambridgepixel.media import discover

        if url != SEED:
            from pipelines.sources.cambridgepixel.imagery import discover as reviewed

            return reviewed(url, body, entities, self.image_matches)

        return discover(url, body, entities)

    def normalize(self, url: str) -> str | None:
        if url in self.seeds[1:]:
            return url
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
            if url in self.seeds:
                from pipelines.sources.cambridgepixel.imagery import validate_page

                validate_page(url, body, self.image_matches)
                return []
            raise ValueError("Outside Cambridge Pixel source scope")
        catalog(body)
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if url != SEED:
            if url in self.seeds:
                from pipelines.sources.cambridgepixel.imagery import extract

                return extract(url, body, self.image_matches, self.catalog_entities)
            raise ValueError("Outside Cambridge Pixel source scope")
        dataset, records, notice = catalog(body)
        attribution = f"Source: Cambridge Pixel, {SEED}\nDatabase last updated: {dataset['dateModified']}.\n{notice}"
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
