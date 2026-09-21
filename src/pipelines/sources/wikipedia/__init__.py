import re
from copy import copy
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.html import text
from pipelines.sources.mediawiki import config

ORIGIN = "https://en.wikipedia.org"
ROOT_CATEGORY = "Category:Military_radars_of_China"
# These category members describe an organization and a national system, not items.
NON_ITEMS = {82932384, 62003409}
LAYOUT = "script, style, form, .navbox, .navbar, .vertical-navbox, .sidebar, .hatnote, .mw-editsection, .mw-cite-backlink, #toc, .toc, .sistersitebox, .sisterproject, .noprint"


def field_text(node: Tag) -> str:
    node = copy(node)
    for reference in node.select(".reference, .mw-ref"):
        reference.decompose()
    for exponent in node.select("sup, sub"):
        exponent.replace_with(
            ("^" if exponent.name == "sup" else "_") + "(" + text(exponent) + ")"
        )
    return text(node)


def lead_paragraphs(content: Tag) -> list[Tag]:
    lead = content.select_one('section[data-mw-section-id="0"]')
    if lead:
        return [p for p in lead.find_all("p", recursive=False) if text(p)]
    paragraphs = []
    for node in content.children:
        if isinstance(node, Tag):
            if node.name in {"h2", "h3"} or node.select_one("h2, h3"):
                break
            if node.name == "p" and text(node):
                paragraphs.append(node)
    return paragraphs


class Wikipedia:
    id = "wikipedia"
    version = "1"
    seeds: tuple[str, ...] = (ORIGIN + "/wiki/" + ROOT_CATEGORY,)
    minimum_entities = 35

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname != "en.wikipedia.org"
            or parts.port not in {None, 80, 443}
            or not parts.path.startswith("/wiki/")
        ):
            return None
        title = unquote(parts.path[6:]).replace(" ", "_")
        if (
            not title
            or any(c in title for c in "\\?#\x00\r\n")
            or any(segment in {".", ".."} for segment in title.split("/"))
            or (":" in title and not title.startswith("Category:"))
        ):
            return None
        query = parse_qsl(parts.query, keep_blank_values=True)
        if query and (
            not title.startswith("Category:")
            or any(
                k not in {"pagefrom", "pageuntil", "subcatfrom", "subcatuntil"}
                for k, _ in query
            )
            or len({k for k, _ in query}) != len(query)
        ):
            return None
        canonical = ORIGIN + "/wiki/" + quote(title, safe="/():,'-._~")
        return canonical + ("?" + urlencode(sorted(query)) if query else "")

    def members(self, url: str, body: bytes) -> dict[str, str]:
        if not unquote(urlsplit(url).path).startswith("/wiki/Category:"):
            return {}
        soup = BeautifulSoup(body, "html.parser")
        if soup.select_one("#mw-pages, #mw-subcategories") is None:
            raise ValueError("Missing Wikipedia category membership")
        links = soup.select(
            "#mw-pages .mw-category-group a[href], #mw-subcategories .CategoryTreeItem a[href]"
        )
        result = {}
        for link in links:
            target = self.normalize(urljoin(url, str(link["href"])))
            if target is None:
                raise ValueError("Unsupported Wikipedia category member URL")
            result[target] = text(link)
        # A blocked or unsupported next-page URL must not silently truncate discovery.
        for link in soup.select("#mw-pages a[href], #mw-subcategories a[href]"):
            if text(link).lower() not in {"next page", "previous page"}:
                continue
            target = self.normalize(urljoin(url, str(link["href"])))
            if target is None:
                raise ValueError("Unsupported Wikipedia category pagination URL")
            result[target] = ""
        return result

    def discover(self, url: str, body: bytes) -> list[str]:
        return sorted(self.members(url, body))

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {
            target: [name]
            for target, name in self.members(url, body).items()
            if "/wiki/Category:" not in target
        }

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if "/wiki/Category:" in url or not names:
            return []
        soup = BeautifulSoup(body, "html.parser")
        page_id = config(soup, "wgArticleId")
        revision = config(soup, "wgRevisionId")
        if (
            type(page_id) is not int
            or type(revision) is not int
            or page_id <= 0
            or revision <= 0
            or config(soup, "wgNamespaceNumber") != 0
        ):
            raise ValueError("Invalid Wikipedia article identity")
        if page_id in NON_ITEMS:
            return []
        heading = soup.select_one("#firstHeading")
        content = soup.select_one("#mw-content-text .mw-parser-output")
        canonical_node = soup.select_one('link[rel="canonical"]')
        license_node = soup.select_one('link[rel="license"]')
        canonical = (
            self.normalize(str(canonical_node.get("href", "")))
            if canonical_node
            else None
        )
        if (
            heading is None
            or content is None
            or canonical is None
            or license_node is None
        ):
            raise ValueError("Incomplete Wikipedia article or attribution")
        title = text(heading)
        license_url = str(license_node.get("href", ""))
        if not license_url.startswith("https://creativecommons.org/licenses/by-sa/"):
            raise ValueError("Unrecognized Wikipedia content license")
        for node in content.select(LAYOUT):
            node.decompose()
        paragraphs = lead_paragraphs(content)
        if not paragraphs:
            raise ValueError("Wikipedia article has no lead description")
        if content.select_one(".ib-aircraft"):
            kind = EntityKind.AIRCRAFT
        elif re.search(r"\bradar\b", title, re.I) or re.search(
            r"\b(?:is|was|are|were)\b.{0,250}\bradar\b", text(paragraphs[0]), re.I
        ):
            kind = EntityKind.RADAR
        else:
            raise ValueError(f"Unreviewed Wikipedia entity kind: {title}")
        # Only subject names before the lead's first copula are candidate aliases.
        # Bold text later in a paragraph can name a predecessor or a different system.
        first = paragraphs[0]
        subject = re.split(r"\b(?:is|was|are|were)\b", text(first), maxsplit=1)[0]
        aliases = {title}
        for node in first.select("b, strong"):
            value = text(node).strip(" ,;\"'")
            if value and len(value) <= 120 and text(node) in subject:
                aliases.add(value)
        for node in content.select(".infobox-title"):
            value = field_text(node)
            if value and len(value) <= 120:
                aliases.add(value)
        categories = sorted({text(a) for a in soup.select("#mw-normal-catlinks li a")})
        revision_url = ORIGIN + "/w/index.php?" + urlencode({"oldid": revision})
        history_url = (
            ORIGIN
            + "/w/index.php?"
            + urlencode(
                {"title": unquote(urlsplit(canonical).path[6:]), "action": "history"}
            )
        )
        facts = [
            Fact("Wikipedia page ID", str(page_id), url),
            Fact("Wikipedia revision ID", str(revision), url),
            Fact("Canonical article", canonical, url),
            Fact("Revision URL", revision_url, url),
            Fact("License", license_url, url),
        ]
        for row in content.select(".infobox tr"):
            label = row.find("th", recursive=False)
            cell = row.find("td", recursive=False)
            if label is not None and cell is not None and field_text(cell):
                facts.append(
                    Fact(
                        field_text(label),
                        field_text(cell),
                        url,
                        qualifier="Wikipedia infobox; source qualifications and variant names retained",
                    )
                )
        for node in content.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    target = urljoin(canonical, str(node[attr]))
                    if urlsplit(target).scheme in {"http", "https"}:
                        node[attr] = target
                    else:
                        del node[attr]
        markdown = markdownify(
            str(content), heading_style="ATX", sub_symbol="<sub>", sup_symbol="<sup>"
        ).strip()
        search = copy(content)
        for node in search.select(
            ".reference, .mw-ref, .reflist, .references, .ambox, .metadata"
        ):
            node.decompose()
        # Bibliography and link lists remain in full evidence but not embeddings.
        for heading_node in list(search.select("h2, h3")):
            if heading_node.parent is None:
                continue
            if text(heading_node).lower() in {
                "references",
                "notes",
                "citations",
                "bibliography",
                "sources",
                "external links",
                "see also",
                "further reading",
            }:
                section = heading_node.find_parent("section")
                if section is not None:
                    section.decompose()
                else:
                    parent = heading_node.parent
                    container = (
                        parent
                        if "mw-heading" in str(parent.get("class", ""))
                        else heading_node
                    )
                    for sibling in list(container.next_siblings):
                        if isinstance(sibling, Tag) and (
                            sibling.name in {"h2", "h3"} or sibling.select_one("h2, h3")
                        ):
                            break
                        sibling.extract()
                    container.decompose()
        return [
            Entity(
                key=str(page_id),
                title=title,
                kind=kind,
                aliases=sorted(aliases),
                categories=categories,
                facts=facts,
                evidence=[
                    Evidence(
                        url=url,
                        title=title,
                        markdown=markdown,
                        search_text=title + "\n\n" + field_text(search),
                        links=sorted(
                            {str(n["href"]) for n in content.select("a[href]")}
                        ),
                        attribution=f"Wikipedia contributors. [Revision {revision}]({revision_url}); [author history]({history_url}); [CC BY-SA]({license_url}). HTML converted to Markdown with navigation removed. Source claims and qualifications retained; linked media have their own licenses.",
                    )
                ],
            )
        ]
