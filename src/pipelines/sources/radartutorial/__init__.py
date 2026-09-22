import re
from collections.abc import Iterable
from copy import copy
from typing import TYPE_CHECKING
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact, evidence_id
from pipelines.sources.html import resolve_links, text

if TYPE_CHECKING:
    from pipelines.media import MediaCandidate

ORIGIN = "https://www.radartutorial.eu"


def parse_html(body: bytes) -> BeautifulSoup:
    # A few pages leave a language-menu comment open across the content boundary.
    repaired = re.sub(
        rb"(<!--(?:(?!-->).)*?)(</nav>\s*<div\s+class=[\"']content[\"'])",
        rb"\1-->\2",
        body,
        flags=re.S | re.I,
    )
    return BeautifulSoup(repaired, "html.parser")


def fact_text(node: Tag) -> str:
    if node.find(["sup", "sub"]) is None:
        return text(node)
    node = copy(node)
    for script in node.select("sup, sub"):
        symbol = "^" if script.name == "sup" else "_"
        script.replace_with(f"{symbol}({text(script)})")
    return text(node)


def normalize_fact(name: str, raw: str, evidence: str) -> Fact:
    fact = Fact(name, raw, evidence)
    # Only normalize unambiguous simple values and ranges. Keep all source text.
    value = raw.replace("\u202f", "").replace("\xa0", " ").strip()
    match = re.fullmatch(
        r"(\d+(?:[.,]\d+)*)\s*(?:([–−\u2026-]|\.{3})\s*(\d+(?:[.,]\d+)*))?\s*(GHz|MHz|kHz|Hz|km|m|kW|W|µs|μs|ms|ns|rpm|°)",
        value,
    )
    if not match:
        return fact

    def number(part: str) -> float:
        if re.fullmatch(r"\d{1,3}(?:,\d{3})+", part):
            return float(part.replace(",", ""))
        return float(part.replace(",", "."))

    try:
        fact.values = [number(match[1])]
        if match[3]:
            fact.values.append(number(match[3]))
        fact.unit = match[4]
        fact.qualifier = "range" if match[3] else "value"
    except ValueError:
        fact.values = []
    return fact


class Radartutorial:
    id = "radartutorial"
    version = "1"
    minimum_entities = 1500
    media_origins = (ORIGIN,)
    media_workers = 2
    media_request_interval = 0.3

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> Iterable["MediaCandidate"]:
        from pipelines.sources.radartutorial.media import candidates

        return candidates(url, body, entities)

    seeds: tuple[str, ...] = (
        f"{ORIGIN}/sitemap.en.xml",
        f"{ORIGIN}/index.en.html",
        f"{ORIGIN}/19.kartei/en/ka03.en.html",
        f"{ORIGIN}/html/sm03.en.html",
        f"{ORIGIN}/html/feed.en.html",
        f"{ORIGIN}/html/abbr.en.html",
        f"{ORIGIN}/logos/hersteller.html",
        f"{ORIGIN}/html/copyright.en.html",
    )

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if parts.scheme not in {"https", "http"} or parts.hostname not in {
            "www.radartutorial.eu",
            "radartutorial.eu",
        }:
            return None
        path = parts.path
        decoded = unquote(path)
        if any(segment in {"..", "."} for segment in decoded.split("/")):
            return None
        if decoded.startswith(("/bin/", "/30.datenbank/", "/temp/", "/temp1/")):
            return None
        if not (
            path.endswith(".en.html")
            or path in {"/sitemap.en.xml", "/logos/hersteller.html"}
        ):
            return None
        return urlunsplit(
            ("https", "www.radartutorial.eu", quote(decoded, safe="/-._~"), "", "")
        )

    def discover(self, url: str, body: bytes) -> list[str]:
        parser = "xml" if url.endswith(".xml") else "html.parser"
        soup = BeautifulSoup(body, "xml") if parser == "xml" else parse_html(body)
        candidates = (
            [node.get_text() for node in soup.find_all("loc")]
            if parser == "xml"
            else [str(node.get("href", "")) for node in soup.find_all("a", href=True)]
        )
        if url.endswith("/19.kartei/en/ka02.en.html"):
            # This navigation page chooses a random radar from a literal JS array.
            candidates.extend(
                re.findall(
                    r'["\']([^"\']+\.en\.html)["\']',
                    body.decode("utf-8", errors="replace"),
                )
            )
        return sorted(
            {
                normalized
                for link in candidates
                if (normalized := self.normalize(urljoin(url, link))) is not None
            }
        )

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        if (
            not re.search(r"/19\.kartei/en/ka\d+\.en\.html$", url)
            and "/logos/" not in url
        ):
            return {}
        soup = parse_html(body)
        labels: dict[str, list[str]] = {}
        for anchor in soup.select("a[href]"):
            target = self.normalize(urljoin(url, str(anchor["href"])))
            if not target or "/karte" not in target:
                continue
            label = str(anchor.get("title", "")) or text(anchor)
            if label and not label.isdigit() and len(label) < 150:
                labels.setdefault(target, []).append(label)
        return labels

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if not re.search(r"/19\.kartei/[^/]+/en/karte\d+\.en\.html$", url):
            return []
        # This inventory URL is an event report, not an equipment record.
        if url.endswith("/13.labs/en/karte001.en.html"):
            return []
        soup = parse_html(body)
        robots = soup.find("meta", attrs={"name": re.compile("^robots$", re.I)})
        if robots and "noindex" in str(robots.get("content", "")).lower():
            return []
        content = soup.select_one("div.content")
        if content is None:
            raise ValueError(f"Missing content container: {url}")
        heading = content.select_one(
            ".fliesstext :is(h1, h2, h3, h4, h5, h6)"
        ) or content.select_one(
            "h1:not(.hh_yes), h2:not(.hh_yes), h3:not(.hh_yes), h4:not(.hh_yes), h5:not(.keywords):not(.hh_yes), h6:not(.keywords):not(.hh_yes)"
        )
        title = text(heading) if heading else text(soup.title) if soup.title else url
        title = re.sub(
            r"\s*[-|]\s*(?:Radar Basics|Radartutorial).*$", "", title, flags=re.I
        ).strip()
        facts: list[Fact] = []
        for row in content.select("table.ttd tr"):
            cells = row.find_all(["td", "th"], recursive=False)
            if len(cells) < 2:
                continue
            label, value = fact_text(cells[0]).strip(": "), fact_text(cells[-1])
            if not label or not value or value in {"?", "-", "−"}:
                continue
            for span in cells[-1].select("[title]"):
                tip = str(span["title"])
                if tip and tip not in value:
                    value += f" ({text(span)}: {tip})"
            cell_id = str(cells[-1].get("id", ""))
            evidence = f"{url}#{cell_id}" if cell_id else url
            facts.append(normalize_fact(label, value, evidence))
        for duplicate in content.select(".hh_yes"):
            if duplicate.name in {
                "h1",
                "h2",
                "h3",
                "h4",
                "h5",
                "h6",
            } and duplicate.find(["section", "table", "div", "p"]):
                # An unclosed mobile heading can incorrectly enclose the entire article.
                duplicate.unwrap()
            else:
                duplicate.decompose()
        for element in content.select(
            "script, style, nav, .prn, .werbung, iframe, form"
        ):
            element.decompose()
        # Footnotes may sit outside the main content container.
        for reference in soup.select("p.source, ul.source, ol.source"):
            if content not in reference.parents:
                content.append(reference.extract())
        resolve_links(content, url)
        for explanation in content.select(".explan[title]"):
            explanation.append(f" ({explanation['title']})")
        for anchor in content.select("a[title]"):
            if not text(anchor) and anchor.find("img") is None:
                anchor.string = " ".join(str(anchor["title"]).split())
                del anchor["title"]
        for image in content.select("img"):
            if not image.get("alt"):
                image["alt"] = "Diagram or equation (see original image)"
            else:
                image["alt"] = " ".join(str(image["alt"]).split())
        markdown = markdownify(
            str(content),
            heading_style="ATX",
            keep_inline_images_in=["td", "th"],
            sub_symbol="<sub>",
            sup_symbol="<sup>",
        )
        markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()
        if len(markdown) < 40:
            raise ValueError(f"Empty extracted content: {url}")
        category = urlsplit(url).path.split("/")[2]
        kind = (
            EntityKind.EQUIPMENT
            if category in {"12.ecm", "13.labs"}
            else EntityKind.RADAR
        )
        links = sorted({str(anchor["href"]) for anchor in content.select("a[href]")})
        page = Evidence(
            url=url,
            title=title,
            markdown=markdown,
            links=links,
            attribution=(
                "Radartutorial / Christian Wolff. Text licensing and individual image credits: "
                "https://www.radartutorial.eu/html/copyright.en.html"
            ),
        )
        aliases = {title, *names}
        for name in list(aliases):
            aliases.update(re.findall(r'[“"«]([^”"»]+)[”"»]', name))
        return [
            Entity(
                key=evidence_id(url),
                title=title,
                kind=kind,
                evidence=[page],
                aliases=sorted(aliases),
                categories=[category],
                facts=facts,
            )
        ]
