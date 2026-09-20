"""Reviewed Military Wiki radar identities from its public MediaWiki API."""

import json
import re
from collections.abc import Iterable
from copy import copy
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.mediawiki import text

ORIGIN = "https://military-history.fandom.com"
CATEGORY = "Category:Russian_and_Soviet_military_radars"
API = ORIGIN + "/api.php"


def api(**params: str) -> str:
    return API + "?" + urlencode(sorted({"format": "json", **params}.items()))


def article_url(page_id: str) -> str:
    return api(action="parse", pageid=page_id, prop="text|revid|categories")


SEED = api(action="query", list="categorymembers", cmtitle=CATEGORY, cmlimit="500")
RIGHTS = api(action="query", meta="siteinfo", siprop="rightsinfo")
LAYOUT = "script, style, form, .navbox, .navbar, .sidebar, .hatnote, .mw-editsection, .mw-cite-backlink, #toc, .toc, .noprint"


def payload(body: bytes) -> dict:
    data = json.loads(body)
    if not isinstance(data, dict) or "error" in data or "errors" in data:
        raise ValueError("Invalid Fandom API response")
    return data


def prose(content: Tag) -> list[Tag]:
    result = []
    for node in content.children:
        if not isinstance(node, Tag):
            continue
        if node.name in {"h2", "h3"}:
            break
        if node.name == "p" and text(node) and not node.select_one(".coordinates"):
            if node.get("id") != "coordinates" and not text(node).startswith(
                "Coordinates:"
            ):
                result.append(node)
    return result


def normalize_media(content: Tag, soup: BeautifulSoup) -> None:
    """Retain file links and captions independently of transient thumbnail failures."""
    for icon in list(content.select(".info-icon, .flagicon")):
        if icon.parent is not None:
            icon.decompose()
    for media in list(content.select("img, .mw-broken-media")):
        if media.parent is None:
            continue
        link = media.find_parent("a")
        if link is not None and link.parent is None:
            continue
        name = str(media.get("data-image-name", ""))
        if not name and link is not None:
            name = str(link.get("title", "")).removeprefix("File:")
        if name:
            name = name.replace("_", " ")
            replacement = soup.new_tag(
                "a",
                href=ORIGIN
                + "/wiki/File:"
                + quote(name.replace(" ", "_"), safe="():,'-._~"),
            )
            replacement.string = "File:" + name
            (link if link is not None else media).replace_with(replacement)
        elif media.name == "img" and not media.get("alt"):
            media.decompose()


def searchable(content: Tag) -> str:
    result = copy(content)
    for node in list(
        result.select(
            ".reference, .reflist, .references, .ambox, .metadata, #coordinates"
        )
    ):
        if node.parent is not None:
            node.decompose()
    for heading in list(result.select("h2, h3")):
        if heading.parent is None:
            continue
        name = text(heading).lower().strip()
        if name in {
            "references",
            "notes",
            "citations",
            "bibliography",
            "sources",
            "external links",
            "see also",
            "further reading",
        }:
            for sibling in list(heading.next_siblings):
                if isinstance(sibling, Tag) and sibling.name in {"h2", "h3"}:
                    break
                sibling.extract()
            heading.decompose()
    for node in list(result.find_all(["p", "table", "div"])):
        if node.parent is not None and text(node).startswith(
            "All or a portion of this article"
        ):
            node.decompose()
    for link in result.select('a[href*="/wiki/File:"]'):
        link.decompose()
    return markdownify(str(result), heading_style="ATX").strip()


class Fandom:
    id = "fandom"
    version = "1"
    seeds: tuple[str, ...] = (SEED, RIGHTS)
    minimum_entities = 46

    def __init__(self) -> None:
        self.catalog: dict[str, dict] = json.loads(
            Path(__file__).with_name("fandom_pages.json").read_text(encoding="utf-8")
        )
        self.members: dict[str, str] = {}
        self.license: dict[str, str] = {}

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"https", "http"}
            or parts.netloc != "military-history.fandom.com"
            or parts.path != "/api.php"
        ):
            return None
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        params = dict(pairs)
        if len(params) != len(pairs) or params.get("format") != "json":
            return None
        if params.get("action") == "parse":
            if (
                set(params) != {"action", "format", "pageid", "prop"}
                or params["prop"] != "text|revid|categories"
                or not re.fullmatch(r"[1-9][0-9]{0,12}", params["pageid"])
            ):
                return None
        elif params.get("list") == "categorymembers":
            base = dict(parse_qsl(urlsplit(SEED).query))
            if any(params.get(k) != v for k, v in base.items()) or set(
                params
            ) - base.keys() - {"cmcontinue", "continue"}:
                return None
            if "continue" in params and "cmcontinue" not in params:
                return None
            if any(
                not value or len(value) > 2048 or any(ord(c) < 32 for c in value)
                for key, value in params.items()
                if key in {"continue", "cmcontinue"}
            ):
                return None
        elif params != dict(parse_qsl(urlsplit(RIGHTS).query)):
            return None
        return api(**params)

    def category(self, body: bytes) -> tuple[dict[str, str], str | None]:
        data = payload(body)
        items = data.get("query", {}).get("categorymembers")
        if not isinstance(items, list) or not items:
            raise ValueError("Missing Fandom category membership")
        result = {}
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("ns") != 0
                or type(item.get("pageid")) is not int
                or item["pageid"] <= 0
                or not isinstance(item.get("title"), str)
                or not item["title"].strip()
            ):
                raise ValueError("Unsupported Fandom category member; review scope")
            key = str(item["pageid"])
            if key in result:
                raise ValueError("Duplicate Fandom category member")
            result[key] = item["title"]
        next_url = None
        if "continue" in data:
            continuation = data["continue"]
            if (
                not isinstance(continuation, dict)
                or "cmcontinue" not in continuation
                or set(continuation) - {"cmcontinue", "continue"}
                or not all(isinstance(v, str) for v in continuation.values())
            ):
                raise ValueError("Unsupported Fandom API continuation")
            params = dict(parse_qsl(urlsplit(SEED).query)) | continuation
            next_url = self.normalize(api(**params))
            if next_url is None:
                raise ValueError("Invalid Fandom API continuation")
        return result, next_url

    def discover(self, url: str, body: bytes) -> list[str]:
        if self.normalize(url) != url:
            raise ValueError("Outside Fandom API scope")
        if "list=categorymembers" not in url:
            payload(body)
            return []
        members, next_url = self.category(body)
        return sorted(
            [article_url(key) for key in members] + ([next_url] if next_url else [])
        )

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        if "list=categorymembers" not in url:
            return {}
        return {
            article_url(key): [title] for key, title in self.category(body)[0].items()
        }

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        self.members, self.license = {}, {}
        saved = dict(pages)
        pending = [SEED]
        seen: set[str] = set()
        while pending:
            url = pending.pop()
            if url in seen:
                raise ValueError("Cyclic Fandom category continuation")
            seen.add(url)
            if url not in saved:
                raise ValueError("Incomplete Fandom category continuation")
            members, next_url = self.category(saved[url])
            if self.members.keys() & members.keys():
                raise ValueError("Repeated Fandom member across continuation")
            self.members.update(members)
            if next_url:
                pending.append(next_url)
        if set(self.members) != set(self.catalog) or any(
            self.catalog[key]["title"] != title for key, title in self.members.items()
        ):
            raise ValueError("Fandom category changed; review the identity catalog")
        if any(article_url(key) not in saved for key in self.members):
            raise ValueError("Incomplete Fandom article capture")
        rights = (
            payload(saved.get(RIGHTS, b"{}")).get("query", {}).get("rightsinfo", {})
        )
        if (
            not isinstance(rights.get("text"), str)
            or not rights["text"]
            or not isinstance(rights.get("url"), str)
            or not rights["url"].startswith("https://")
        ):
            raise ValueError("Missing Fandom license attribution")
        self.license = rights

    def extract(self, url: str, body: bytes, labels: list[str]) -> list[Entity]:
        if self.normalize(url) != url:
            raise ValueError("Outside Fandom API scope")
        params = dict(parse_qsl(urlsplit(url).query))
        if params["action"] != "parse":
            self.discover(url, body)
            return []
        key = params["pageid"]
        if key not in self.members or not self.license:
            raise ValueError("Fandom extraction requires prepared category and license")
        data = payload(body).get("parse", {})
        fragment = data.get("text", {}).get("*")
        if (
            type(data.get("pageid")) is not int
            or str(data["pageid"]) != key
            or data.get("title") != self.members[key]
            or type(data.get("revid")) is not int
            or data["revid"] <= 0
            or not isinstance(fragment, str)
        ):
            raise ValueError("Invalid Fandom article identity or rendered HTML")
        categories = data.get("categories")
        if not isinstance(categories, list) or any(
            not isinstance(c, dict) or not isinstance(c.get("*"), str)
            for c in categories
        ):
            raise ValueError("Invalid Fandom article categories")
        canonical = (
            ORIGIN + "/wiki/" + quote(data["title"].replace(" ", "_"), safe="():,'-._~")
        )
        soup = BeautifulSoup(fragment, "html.parser")
        content = soup.select_one(".mw-parser-output")
        if content is None:
            raise ValueError("Missing Fandom article body")
        for node in list(content.select(LAYOUT)):
            if node.parent is not None:
                node.decompose()
        normalize_media(content, soup)
        for link in content.select("a[href], img[src]"):
            attr = "href" if link.name == "a" else "src"
            link[attr] = urljoin(canonical, str(link[attr]))
        lead = prose(content)
        if not lead or sum(len(text(p)) for p in lead) < 50:
            raise ValueError("Missing Fandom subject introduction")
        subject = self.catalog[self.catalog[key]["entity"]]
        aliases = {data["title"]}
        # Only bold subject names before the first copula qualify as lead aliases.
        first = text(lead[0])
        prefix = re.split(r"\b(?:is|was|are|were)\b", first, maxsplit=1)[0]
        aliases.update(
            text(node) for node in lead[0].select("b, strong") if text(node) in prefix
        )
        body_text = text(content).casefold()
        aliases.update(
            name for name in subject.get("aliases", []) if name.casefold() in body_text
        )
        facts = [
            Fact("Wiki page ID", key, canonical),
            Fact("Wiki revision ID", str(data["revid"]), canonical),
        ]
        for row in content.select("table.infobox tr"):
            cells = row.find_all(["th", "td"], recursive=False)
            if len(cells) == 2 and all(text(cell) for cell in cells):
                facts.append(Fact(text(cells[0]), text(cells[1]), canonical))
        revision_url = (
            ORIGIN
            + "/index.php?"
            + urlencode({"title": data["title"], "oldid": data["revid"]})
        )
        history_url = canonical + "?action=history"
        attribution = f"Military Wiki (Fandom) contributors, page {key}, revision {data['revid']}. Revision: {revision_url}. History: {history_url}. Wiki license label: {self.license['text']} ({self.license['url']}). Original article and imported attribution notices are retained below; media may have separate licenses. HTML was rendered by the public MediaWiki API; the original JSON response is retained separately. Markdown normalizes media links and removes layout controls."
        rendered = f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{escape(data["title"])}</title><base href="{escape(canonical, quote=True)}"><link rel="canonical" href="{escape(canonical, quote=True)}"></head><body>{fragment}</body></html>\n'
        entity = Entity(
            key=self.catalog[key]["entity"],
            title=subject["title"],
            kind=EntityKind(subject["kind"]),
            aliases=sorted(aliases - {subject["title"]}),
            categories=sorted(
                {
                    c["*"].replace("_", " ")
                    for c in categories
                    if "hidden" not in c
                    and not c["*"].startswith(("Articles_", "All_", "Pages_", "CS1_"))
                }
            ),
            facts=facts,
            url=canonical if key == self.catalog[key]["entity"] else "",
            evidence=[
                Evidence(
                    url=url,
                    title=data["title"],
                    canonical_url=canonical,
                    rendered_html=rendered,
                    markdown=markdownify(str(content), heading_style="ATX").strip(),
                    search_text=searchable(content),
                    links=sorted(
                        {
                            str(a["href"])
                            for a in content.select("a[href]")
                            if urlsplit(str(a["href"])).scheme in {"http", "https"}
                        }
                    ),
                    attribution=attribution,
                )
            ],
        )
        entity.validate()
        return [entity]
