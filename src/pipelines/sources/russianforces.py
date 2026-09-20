import json
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.feeds import previous_urls
from pipelines.sources.html import text

ORIGIN = "https://russianforces.org"
FEED = ORIGIN + "/atom.xml"
ARTICLE = re.compile(r"/blog/\d{4}/\d{2}/[a-z0-9_-]+\.shtml$")
ATOM = "{http://www.w3.org/2005/Atom}"
COSMOS = re.compile(r"(?<![\w-])Cosmos[- ](\d{1,5})(?![\w-])", re.I)
CATALOG = json.loads(
    Path(__file__).with_name("russianforces_entities.json").read_text()
)


def pattern(aliases: list[str]) -> re.Pattern:
    return re.compile(
        r"(?<![\w-])(?:"
        + "|".join(re.escape(a) for a in sorted(aliases, key=len, reverse=True))
        + r")(?![\w-])",
        re.I,
    )


def cells(row: Tag) -> list[str]:
    # Some source tables omit closing tags around their empty spacer cells.
    return [
        text(cell)
        for cell in row.find_all(["td", "th"])
        if not cell.find(["td", "th"]) and text(cell)
    ]


class RussianForces:
    id = "russianforces"
    version = "1"
    seeds: tuple[str, ...] = (FEED,)
    minimum_entities = 30

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in {"russianforces.org", "www.russianforces.org"}
            or parts.port not in {None, 80, 443}
        ):
            return None
        path = unquote(parts.path)
        if path != "/atom.xml" and not ARTICLE.fullmatch(path):
            return None
        return urlunsplit(("https", "russianforces.org", path, "", ""))

    def discover(self, url: str, body: bytes) -> list[str]:
        if url != FEED:
            return []
        root = ElementTree.fromstring(body)
        entries = root.findall(ATOM + "entry") if root.tag == ATOM + "feed" else []
        if not entries:
            raise ValueError("RussianForces returned no Atom entries")
        urls = set()
        for entry in entries:
            link = entry.find(f'{ATOM}link[@rel="alternate"]')
            target = self.normalize(link.get("href", "")) if link is not None else None
            if target is None or target == FEED:
                raise ValueError("Atom entry has no in-scope article URL")
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
        entry = soup.select_one('.entry-asset[id^="entry-"]')
        heading = entry.select_one(".asset-name") if entry else None
        content = entry.select_one(".asset-content") if entry else None
        if entry is None or heading is None or content is None or not text(content):
            raise ValueError("Incomplete RussianForces article")
        article_id = str(entry.get("id", "")).removeprefix("entry-")
        if not article_id.isdigit():
            raise ValueError("Missing source article ID")
        metadata = {}
        for name in ("citation_author", "citation_publication_date"):
            node = soup.select_one(f'meta[name="{name}"]')
            if node is None or not node.get("content"):
                raise ValueError("Article is missing attribution or publication date")
            metadata[name] = str(node["content"])
        for node in content.select("script, style, form"):
            node.decompose()
        for node in content.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    target = urljoin(url, str(node[attr]))
                    if urlsplit(target).scheme in {"http", "https"}:
                        node[attr] = target
                    else:
                        del node[attr]
        blocks = [
            heading,
            *[
                n
                for n in content.find_all(["p", "li", "tr"])
                if not n.find(["p", "li", "tr"])
            ],
        ]
        definitions = list(CATALOG)
        numbers = sorted(
            {match[1] for block in blocks for match in COSMOS.finditer(text(block))},
            key=int,
        )
        definitions.extend(
            {
                "key": "cosmos-" + number,
                "title": "Cosmos-" + number,
                "kind": "spacecraft",
                "category": "Reported satellite identities",
                "aliases": ["Cosmos-" + number, "Cosmos " + number],
            }
            for number in numbers
        )
        title = text(heading)
        evidence = Evidence(
            url=url,
            title=title,
            markdown=markdownify(str(content), heading_style="ATX").strip(),
            links=sorted({str(n["href"]) for n in content.select("a[href]")}),
            attribution=f"{metadata['citation_author']}, Russian Strategic Nuclear Forces. Published: {metadata['citation_publication_date']}. Source assessments and uncertainty retained as published.",
        )
        entities = []
        for definition in definitions:
            matcher = pattern(definition["aliases"])
            selected = [
                block
                for block in blocks
                if matcher.search(text(block))
                and (
                    not definition.get("context")
                    or re.search(definition["context"], text(block), re.I)
                )
            ]
            if not selected:
                continue
            facts = [
                Fact("Article ID", article_id, url),
                Fact("Published", metadata["citation_publication_date"], url),
                Fact("Author", metadata["citation_author"], url),
            ]
            passages = []
            for block in selected:
                passage = text(block)
                facts.append(
                    Fact(
                        "Source passage",
                        passage,
                        url,
                        qualifier="Source assessment; see the dated full article for qualifications and updates",
                    )
                )
                if block.name == "tr":
                    values = cells(block)
                    table = block.find_parent("table")
                    headers = [text(n) for n in table.select("th")] if table else []
                    if headers and len(headers) == len(values):
                        row = list(zip(headers, values, strict=True))
                    elif (
                        len(values) == 3
                        and re.fullmatch(r"\d{4}-\d{3}[A-Z]", values[0])
                        and values[2].isdigit()
                    ):
                        row = list(
                            zip(
                                [
                                    "International designator",
                                    "Source object label",
                                    "NORAD ID",
                                ],
                                values,
                                strict=True,
                            )
                        )
                    else:
                        row = []
                    for label, value in row:
                        facts.append(
                            Fact(
                                label,
                                value,
                                url,
                                qualifier="Reported table value; article may contain later or tentative identifications",
                            )
                        )
                    passage = (
                        "; ".join(f"{label}: {value}" for label, value in row)
                        if row
                        else passage
                    )
                passages.append(passage)
            aliases = [
                alias
                for alias in definition["aliases"]
                if any(pattern([alias]).search(text(block)) for block in selected)
            ]
            entities.append(
                Entity(
                    key=definition["key"],
                    title=definition["title"],
                    kind=EntityKind(definition["kind"]),
                    aliases=sorted(set([definition["title"], *aliases])),
                    categories=[definition["category"]],
                    facts=facts,
                    evidence=[
                        replace(
                            evidence,
                            search_text=f"{definition['title']} | {definition['category']}\n\n"
                            + "\n\n".join(passages),
                        )
                    ],
                )
            )
        return entities
