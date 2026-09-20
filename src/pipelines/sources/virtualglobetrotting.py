import math
import re
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.feeds import previous_urls
from pipelines.sources.html import text

ORIGIN = "https://virtualglobetrotting.com"
CATEGORY = ORIGIN + "/category/buildings/radar-sites/"
FEED = CATEGORY + "rss.xml"
DETAIL = re.compile(r"/map/[a-z0-9][a-z0-9-]*/$")


def markdown(node: Tag) -> str:
    return markdownify(str(node), heading_style="ATX").strip()


class VirtualGlobetrotting:
    id = "virtualglobetrotting"
    version = "1"
    seeds: tuple[str, ...] = (FEED,)
    minimum_entities = 90

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname
            not in {"virtualglobetrotting.com", "www.virtualglobetrotting.com"}
            or parts.port not in {None, 80, 443}
        ):
            return None
        path = unquote(parts.path)
        if path != urlsplit(FEED).path:
            path = path.rstrip("/") + "/"
            if not DETAIL.fullmatch(path):
                return None
        return urlunsplit(("https", "virtualglobetrotting.com", path, "", ""))

    def discover(self, url: str, body: bytes) -> list[str]:
        if url != FEED:
            return []
        root = ElementTree.fromstring(body)
        items = root.findall("./channel/item") if root.tag == "rss" else []
        if not items:
            raise ValueError("VirtualGlobetrotting returned no RSS items")
        urls = set()
        for item in items:
            target = self.normalize(item.findtext("link", ""))
            if target is None or target == FEED:
                raise ValueError("RSS item has no in-scope detail URL")
            urls.add(target)
        return sorted(urls)

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def discovery_seeds(self, directory: Path) -> list[str]:
        return previous_urls(self, directory)

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if url == FEED:
            return []
        soup = BeautifulSoup(body, "html.parser")
        heading = soup.select_one("#content h1")
        article = soup.select_one(".map-info-description")
        description = (
            article.select_one('[itemprop="articleBody"]') if article else None
        )
        place = soup.select_one('[itemtype="https://schema.org/Place"]')
        categories_node = soup.select_one(".map-info-categories")
        if heading is None or place is None or categories_node is None:
            raise ValueError("Incomplete VirtualGlobetrotting detail page")
        if description is None:
            if article is not None or soup.select_one('[itemprop="articleBody"]'):
                raise ValueError("Unexpected VirtualGlobetrotting description layout")
            description = soup.new_tag("div")
        category_links = categories_node.select("li a[href]")
        if not any(urljoin(url, str(a["href"])) == CATEGORY for a in category_links):
            raise ValueError("Detail page is no longer in Radar Sites")
        categories = sorted(
            {
                text(li.find_all("a")[-1])
                for li in categories_node.select("li")
                if li.find_all("a")
            }
        )
        own_link = place.select_one(".map-info-coordinates a[href]")
        identity = (
            re.fullmatch(
                r"/map/(\d+)/nearby/",
                urlsplit(urljoin(url, str(own_link["href"]))).path,
            )
            if own_link
            else None
        )
        if identity is None or not text(heading):
            raise ValueError("Detail page has no stable map ID or title")
        facts = []
        location = []
        for prop, label in [
            ("addressLocality", "Locality"),
            ("addressRegion", "Region"),
            ("addressCountry", "Country"),
        ]:
            node = place.select_one(f'[itemprop="{prop}"]')
            if node and text(node):
                location.append(text(node))
                facts.append(Fact(label, text(node), url))
        for prop, limit in [("latitude", 90), ("longitude", 180)]:
            node = place.select_one(f'meta[itemprop="{prop}"]')
            if node is None:
                raise ValueError("Detail page has no coordinates")
            raw = str(node.get("content", ""))
            value = float(raw)
            if not math.isfinite(value) or not -limit <= value <= limit:
                raise ValueError("Invalid site coordinates")
            facts.append(Fact(prop.title(), raw, url, values=[value], unit="degree"))
        author = ""
        for prop, label in [
            ("author", "Contributor"),
            ("datePublished", "Published"),
            ("dateModified", "Modified"),
        ]:
            node = article.select_one(f'meta[itemprop="{prop}"]') if article else None
            raw = str(node.get("content", "")) if node else ""
            if not raw and prop == "author":
                contributor = soup.select_one(".map-info-by .user-link")
                raw = text(contributor) if contributor else ""
            if not raw and prop in {"datePublished", "dateModified"}:
                field = "published_time" if prop == "datePublished" else "modified_time"
                node = soup.select_one(f'meta[property="article:{field}"]')
                raw = str(node.get("content", "")) if node else ""
            if raw:
                facts.append(Fact(label, raw, url))
                if prop == "author":
                    author = raw
        # Select the complete record, excluding navigation, counters, ads and neighbors.
        fragments = [str(description)]
        for selector in (".map-info-links", ".map-info-thumbs-l", ".map-info-by-line"):
            node = soup.select_one(selector)
            if node:
                fragments.append(str(node))
        comments = soup.select("#comments .comment")
        if comments:
            fragments.append("<h2>Source comments</h2>")
            for comment in comments:
                fragments.extend(
                    str(n)
                    for n in comment.select(
                        ".comment-user, .comment-time, .comment-text"
                    )
                )
        retained = BeautifulSoup("\n".join(fragments), "html.parser")
        for node in retained.select("script, style, form, button, input"):
            node.decompose()
        for node in retained.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    target = urljoin(url, str(node[attr]))
                    if urlsplit(target).scheme in {"http", "https"}:
                        node[attr] = target
                    else:
                        del node[attr]
        title = text(heading)
        location_text = ", ".join(location)
        summary = f"# {title}\n\nRadar site record\n\nLocation: {location_text}\n\nCategories: {', '.join(categories)}\n\n"
        search_text = summary + markdown(description)
        metadata = "\n".join(f"- {fact.name}: {fact.raw}" for fact in facts)
        evidence = Evidence(
            url=url,
            title=title,
            markdown=summary + metadata + "\n\n" + markdown(retained),
            search_text=search_text,
            links=sorted({str(node["href"]) for node in retained.select("a[href]")}),
            attribution=f"VirtualGlobetrotting; contributor: {author or 'not specified'}. Source descriptions and comments are retained as published. Original source and imagery rights apply.",
        )
        return [
            Entity(
                key=identity[1],
                title=title,
                kind=EntityKind.SITE,
                evidence=[evidence],
                aliases=[title],
                categories=categories,
                facts=facts,
            )
        ]
