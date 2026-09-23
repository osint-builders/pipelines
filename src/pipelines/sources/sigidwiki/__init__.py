"""Signal reference records from the identified-signals database table.

The table is the captured evidence. Linked articles, audio and I/Q recordings
are references, not additional captured content or signal identifications.
"""

import hashlib
import re
from copy import copy
from html import escape
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.html import resolve_links, text

ORIGIN = "https://www.sigidwiki.com"
DATABASE = ORIGIN + "/wiki/Database"
HEADERS = (
    "Signal type",
    "Description",
    "Frequency",
    "Mode",
    "Modulation",
    "Bandwidth",
    "Location",
    "Sample Audio",
    "Waterfall image",
)
STATUSES = {
    "#daffdc": "Active",
    "#ffdada": "Inactive",
    "": "Unknown or intermittent",
}
CONTEXT = (
    "Reference from the identified-signals database table. Frequency is the "
    "reported span, not a list of occupied channels. Location is the source's "
    "signal location label, not a transmitter position or country of manufacture. "
    "Status and descriptions are community-reported claims at capture time."
)


def article_url(value: str) -> str:
    parts = urlsplit(urljoin(DATABASE, value))
    if (
        parts.scheme not in {"http", "https"}
        or parts.hostname not in {"sigidwiki.com", "www.sigidwiki.com"}
        or parts.port not in {None, 80, 443}
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or not parts.path.startswith("/wiki/")
    ):
        raise ValueError("Invalid SigIDWiki article URL")
    title = unquote(parts.path[6:]).replace(" ", "_")
    if (
        not title
        or any(c in title for c in "\\?#\x00\r\n")
        or title.split(":", 1)[0].lower()
        in {
            "file",
            "category",
            "special",
            "template",
            "property",
            "form",
            "talk",
            "user",
        }
    ):
        raise ValueError("Invalid SigIDWiki article title")
    if any(part in {".", ".."} for part in title.split("/")):
        raise ValueError("Invalid SigIDWiki article path")
    return ORIGIN + "/wiki/" + quote(title, safe="/(),'!-._~")


def record_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


def clean(node: Tag) -> Tag:
    result = copy(node)
    for tip in result.select(".mw-lingo-tooltip-tip, script, style, .mw-editsection"):
        tip.decompose()
    resolve_links(result, DATABASE, encode_spaces=True)
    return result


def records(body: bytes) -> list[dict]:
    soup = BeautifulSoup(body, "html.parser")
    tables = [
        table
        for table in soup.select("#mw-content-text table.wikitable")
        if tuple(text(th) for th in table.select("tr:first-child > th")) == HEADERS
    ]
    if len(tables) != 1:
        raise ValueError("Missing or ambiguous SigIDWiki database table")
    result: dict[str, dict] = {}
    for row in tables[0].find_all("tr", recursive=False)[1:]:
        cells = row.find_all("td", recursive=False)
        if len(cells) != len(HEADERS):
            raise ValueError("Incomplete SigIDWiki database row")
        link = cells[0].select_one("b > a[href]")
        if link is None or not text(link):
            raise ValueError("Missing SigIDWiki signal identity")
        url = article_url(str(link["href"]))
        color = str(cells[0].get("bgcolor", "")).lower()
        if color not in STATUSES:
            raise ValueError("Unrecognized SigIDWiki status color")
        cleaned = clean(row)
        record = {
            "title": text(link),
            "url": url,
            "status": STATUSES[color],
            "fields": dict(
                zip(HEADERS[1:7], (text(clean(c)) for c in cells[1:7]), strict=True)
            ),
            "audio_urls": sorted({str(n["src"]) for n in cleaned.select("audio[src]")}),
            "row_html": str(cleaned),
        }
        if url in result and record != result[url]:
            raise ValueError("Conflicting duplicate SigIDWiki signal row")
        result[url] = record
    if not result:
        raise ValueError("Empty SigIDWiki database")
    return [result[url] for url in sorted(result)]


class SigIDWiki:
    id = "sigidwiki"
    version = "1"
    seeds = (DATABASE,)
    minimum_entities = 550
    request_interval = 10.0
    download_delay_jitter = 0
    media_origins = (ORIGIN,)
    media_request_interval = 10.0
    media_workers = 1

    def normalize(self, url: str) -> str | None:
        return DATABASE if url == DATABASE else None

    def discover(self, url: str, body: bytes) -> list[str]:
        records(body)  # Fail a blocked/truncated capture before publication.
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if url != DATABASE:
            return []
        entities = []
        for record in records(body):
            title, article = record["title"], record["url"]
            key = record_key(article)
            facts = [Fact("Signal status", record["status"], DATABASE)]
            for name, raw in record["fields"].items():
                label = {
                    "Frequency": "Reported frequency range",
                    "Mode": "Reception mode",
                    "Location": "Signal location",
                }.get(name, name)
                values = raw.split(",") if name in {"Mode", "Modulation"} else [raw]
                facts.extend(Fact(label, value.strip(), DATABASE) for value in values)
            facts.extend(
                Fact("Audio sample URL", target, DATABASE)
                for target in record["audio_urls"]
            )
            aliases = {title}
            aliases.update(re.findall(r"\(([A-Z][A-Z0-9-]{1,15})\)", title))
            table = (
                "<table><tr>"
                + "".join(f"<th>{h}</th>" for h in HEADERS)
                + "</tr>"
                + record["row_html"]
                + "</table>"
            )
            attribution = (
                f"Signal Identification Wiki contributors. Captured [database row]({DATABASE}); "
                f"[signal article]({article}). Glossary tooltips and navigation removed in rendered evidence. "
                "Source text and linked images retain their original attribution and terms; "
                "the database response does not declare a content license."
            )
            rendered = (
                '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                f"<title>{escape(title)}</title></head><body><h1>{escape(title)}</h1>"
                f"<p>{escape(CONTEXT)}</p><p>Status: {escape(record['status'])}</p>"
                f'<p><a href="{escape(DATABASE)}">Database</a> · <a href="{escape(article)}">Signal article</a></p>'
                + table
                + "</body></html>\n"
            )
            entities.append(
                Entity(
                    key=key,
                    title=title,
                    kind=EntityKind.SIGNAL,
                    url=article,
                    aliases=sorted(aliases),
                    categories=["Signal references", record["status"]],
                    facts=facts,
                    evidence=[
                        Evidence(
                            url=DATABASE,
                            canonical_url=article,
                            title=title,
                            markdown=CONTEXT
                            + f"\n\nStatus: {record['status']}\n\n"
                            + markdownify(table, heading_style="ATX").strip(),
                            search_text=title
                            + "\n"
                            + "\n".join(
                                f"{f.name}: {f.raw}"
                                for f in facts
                                if f.name != "Audio sample URL"
                            ),
                            attribution=attribution,
                            links=sorted({article, *record["audio_urls"]}),
                            record_id=key,
                            records=[record],
                            rendered_html=rendered,
                        )
                    ],
                )
            )
        return entities

    def discover_media(self, url: str, body: bytes, entities: list[dict]) -> list:
        from pipelines.sources.sigidwiki.media import discover

        return discover(url, body, entities)

    def audit(
        self, entities: list[dict], responses: dict[str, bytes], html: dict[str, bytes]
    ) -> dict:
        expected = self.extract(DATABASE, responses[DATABASE], [])
        actual = {entity["id"]: entity for entity in entities}
        if set(actual) != {f"sigidwiki:{e.key}" for e in expected}:
            raise ValueError("SigIDWiki database coverage mismatch")
        for entity in expected:
            saved = actual[f"sigidwiki:{entity.key}"]
            metadata = entity.metadata(self.id)
            if any(
                saved[k] != metadata[k]
                for k in ("title", "url", "kind", "facts", "aliases", "categories")
            ):
                raise ValueError("SigIDWiki source facts differ from captured row")
            page = entity.evidence[0]
            if (
                saved["evidence"][0]["records"] != page.records
                or html[page.id] != page.rendered_html.encode()
            ):
                raise ValueError("SigIDWiki record evidence mismatch")
        return {
            "database_records": len(expected),
            "verified_records": len(actual),
            "scope": "identified-signals database rows and their waterfall images",
        }
