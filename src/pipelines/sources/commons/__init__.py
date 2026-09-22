import json
import re
from collections import defaultdict
from collections.abc import Iterable
from copy import copy
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, quote, unquote, urlencode, urljoin, urlsplit

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.html import text
from pipelines.sources.mediawiki import config

if TYPE_CHECKING:
    from pipelines.media import MediaCandidate

ORIGIN = "https://commons.wikimedia.org"
ROOT_CATEGORY = "Category:Military_radars_of_Russia"
LAYOUT = "script, style, form, .mw-editsection, .mw-cite-backlink"


def clean(node: Tag) -> Tag:
    result = copy(node)
    for child in list(result.select(LAYOUT + ", [hidden], [style]")):
        if child.parent is None:
            continue
        style = re.sub(r"\s+", "", str(child.get("style", ""))).lower()
        if child.has_attr("hidden") or "display:none" in style:
            child.decompose()
        elif child.name in {"script", "style", "form"} or set(
            child.get_attribute_list("class")
        ) & {"mw-editsection", "mw-cite-backlink"}:
            child.decompose()
    return result


def english(node: Tag) -> str:
    if node.has_attr("lang") and not str(node["lang"]).lower().startswith("en"):
        return ""
    result = clean(node)
    for child in list(result.select("[lang]")):
        if child.parent is not None and not str(child["lang"]).lower().startswith("en"):
            child.decompose()
    return text(result)


def introduction(content: Tag) -> list[str]:
    """Read prose and lists from legacy summary sections."""
    result = []
    for node in content.children:
        if not isinstance(node, Tag):
            continue
        if node.select_one("#Licensing") or node.get("id") == "Licensing":
            break
        if set(node.get_attribute_list("class")) & {"licensetpl", "layouttemplate"}:
            break
        if node.name in {"p", "ul", "ol", "dl"} and english(node):
            result.append(english(node))
    return result


class Commons:
    id = "commons"
    version = "1"
    seeds: tuple[str, ...] = (ORIGIN + "/wiki/" + ROOT_CATEGORY,)
    minimum_entities = 120
    media_origins: tuple[str, ...] = (
        "https://upload.wikimedia.org",
        "https://thumb.wikimedia.org",
    )
    media_request_interval = 1.0
    media_workers = 4

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> Iterable["MediaCandidate"]:
        from pipelines.sources.commons.media import discover

        return discover(url, body, entities)

    def __init__(self) -> None:
        self.catalog: dict[str, dict[str, str]] = json.loads(
            Path(__file__).with_name("categories.json").read_text(encoding="utf-8")
        )
        self.subjects: dict[str, dict] = {}
        self.owners: dict[str, set[str]] = {}
        self.parents: dict[str, set[str]] = {}
        self.ready = False

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname != "commons.wikimedia.org"
            or parts.port not in {None, 80, 443}
            or not parts.path.startswith("/wiki/")
        ):
            return None
        title = unquote(parts.path[6:]).replace(" ", "_")
        if (
            not title.startswith(("Category:", "File:"))
            or not title.split(":", 1)[1]
            or any(c in title for c in "\\?#\x00\r\n")
            or any(
                segment in {".", ".."} for segment in title.split(":", 1)[1].split("/")
            )
        ):
            return None
        query = parse_qsl(parts.query, keep_blank_values=True)
        if query and (
            not title.startswith("Category:")
            or any(
                k
                not in {
                    "pagefrom",
                    "pageuntil",
                    "subcatfrom",
                    "subcatuntil",
                    "filefrom",
                    "fileuntil",
                }
                for k, _ in query
            )
            or len({k for k, _ in query}) != len(query)
        ):
            return None
        canonical = ORIGIN + "/wiki/" + quote(title, safe="/():,'-._~")
        return canonical + ("?" + urlencode(sorted(query)) if query else "")

    def discover(self, url: str, body: bytes) -> list[str]:
        if "/wiki/Category:" not in url:
            return []
        soup = BeautifulSoup(body, "html.parser")
        if soup.select_one("#mw-subcategories, #mw-category-media, #mw-pages") is None:
            raise ValueError("Missing Commons category membership")
        result = set()
        for link in soup.select(
            "#mw-subcategories .CategoryTreeItem a[href], #mw-category-media .gallerytext a[href]"
        ):
            target = self.normalize(urljoin(url, str(link["href"])))
            if target is None:
                raise ValueError("Unsupported Commons category member URL")
            result.add(target)
        for link in soup.select(
            "#mw-subcategories a[href], #mw-category-media a[href], #mw-pages a[href]"
        ):
            if text(link).lower() not in {"next page", "previous page"}:
                continue
            target = self.normalize(urljoin(url, str(link["href"])))
            if target is None:
                raise ValueError("Unsupported Commons category pagination URL")
            result.add(target)
        return sorted(result)

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def identity(
        self, soup: BeautifulSoup, namespace: int
    ) -> tuple[str, int, str, str]:
        page_id, revision = config(soup, "wgArticleId"), config(soup, "wgRevisionId")
        heading = soup.select_one("#firstHeading")
        canonical_node = soup.select_one('link[rel="canonical"]')
        canonical = (
            self.normalize(str(canonical_node.get("href", "")))
            if canonical_node
            else None
        )
        if (
            type(page_id) is not int
            or page_id <= 0
            or type(revision) is not int
            or revision <= 0
            or config(soup, "wgNamespaceNumber") != namespace
            or heading is None
            or canonical is None
            or (namespace == 14) != ("/wiki/Category:" in canonical)
        ):
            raise ValueError("Invalid Commons page identity")
        title = text(heading).split(":", 1)[-1].strip()
        if not title:
            raise ValueError("Missing Commons title")
        return str(page_id), revision, title, canonical

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        self.ready = False
        self.subjects = {}
        self.owners = {}
        self.parents = defaultdict(set)
        categories: dict[str, str] = {}
        roles: dict[str, str] = {}
        members: dict[str, set[str]] = defaultdict(set)
        for url, body in pages:
            if "/wiki/Category:" not in url:
                continue
            soup = BeautifulSoup(body, "html.parser")
            key, _, title, canonical = self.identity(soup, 14)
            policy = self.catalog.get(key)
            if policy is None or policy["role"] not in {
                "entity",
                "context",
                "collection",
            }:
                raise ValueError(f"Unreviewed Commons category: {key} {title}")
            categories[url.split("?", 1)[0]] = key
            categories[canonical] = key
            roles[key] = policy["role"]
            members[key].update(self.discover(url, body))
            if policy["role"] == "entity":
                caption = soup.select_one("#wdinfoboxcaption")
                aliases = {title}
                if (
                    caption
                    and 0 < len(text(caption)) <= 120
                    and not re.fullmatch(r"Q\d+", text(caption))
                ):
                    aliases.add(text(caption))
                self.subjects[key] = {
                    "title": title,
                    "kind": EntityKind(policy["kind"]),
                    "aliases": sorted(aliases),
                    "url": canonical,
                }
        for parent, links in members.items():
            for link in links:
                if "/wiki/Category:" in link:
                    target = categories.get(link.split("?", 1)[0])
                    if target is None:
                        raise ValueError(f"Missing Commons category in archive: {link}")
                    if target != parent:
                        self.parents[target].add(parent)

        def owners(key: str, trail: frozenset[str] = frozenset()) -> set[str]:
            if key in trail:
                raise ValueError("Cycle in Commons category context")
            if roles[key] == "entity":
                return {key}
            if roles[key] == "collection":
                return set()
            return set().union(
                *(
                    owners(parent, trail | {key})
                    for parent in self.parents.get(key, set())
                )
            )

        for url, key in categories.items():
            self.owners[url] = owners(key)
        file_owners: dict[str, set[str]] = defaultdict(set)
        for key, links in members.items():
            for link in links:
                if "/wiki/File:" in link:
                    file_owners[link].update(owners(key))
        for url, candidates in file_owners.items():
            self.owners[url] = self.specific(candidates)
        self.ready = True

    def specific(self, candidates: set[str]) -> set[str]:
        result = set(candidates)
        for key in candidates:
            visited = {key}
            pending = list(self.parents.get(key, set()))
            while pending:
                parent = pending.pop()
                if parent in visited:
                    continue
                visited.add(parent)
                result.discard(parent)
                pending.extend(self.parents.get(parent, set()))
        return result

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if not self.ready:
            raise ValueError("Commons extraction requires prepared category membership")
        owners = self.owners.get(url.split("?", 1)[0], set())
        if not owners:
            return []
        category = "/wiki/Category:" in url
        soup = BeautifulSoup(body, "html.parser")
        key, revision, title, canonical = self.identity(soup, 14 if category else 6)
        content = soup.select_one("#mw-content-text")
        parser = soup.select_one("#mw-content-text .mw-parser-output")
        license_node = soup.select_one('link[rel="license"]')
        license_url = str(license_node.get("href", "")) if license_node else ""
        if (
            content is None
            or parser is None
            or not license_url.startswith("https://creativecommons.org/licenses/by-sa/")
        ):
            raise ValueError("Incomplete Commons content or text attribution")
        revision_url = ORIGIN + "/w/index.php?" + urlencode({"oldid": revision})
        history_url = (
            ORIGIN
            + "/w/index.php?"
            + urlencode(
                {"title": unquote(urlsplit(canonical).path[6:]), "action": "history"}
            )
        )
        attribution = (
            f"Wikimedia Commons contributors. [Source]({canonical}); "
            f"[revision {revision}]({revision_url}); [contributors/history]({history_url}). "
            f"Page text: [CC BY-SA]({license_url}); converted to Markdown. "
            "Media files have their own licenses, retained in the description; media bytes are not included."
        )
        facts = [
            Fact("Commons page ID", key, url),
            Fact("Commons revision ID", str(revision), url),
            Fact("Canonical page", canonical, url),
            Fact("Revision URL", revision_url, url),
            Fact("Page text license", license_url, url),
        ]
        search: list[str] = []
        if category:
            search.extend(introduction(parser))
            search.extend(
                english(node) for node in parser.select('.description[lang="en"]')
            )
            box = parser.select_one("#wdinfobox")
            if box:
                selected = clean(box)
                for node in selected.select(
                    "#wdinfo_ac, .wikidata-link, .wdinfobox-edit"
                ):
                    node.decompose()
                for node in selected.select("a[href]"):
                    if text(node) in {"Upload media", "Wikipedia"}:
                        node.decompose()
                search.append(english(selected))
                for row in box.select("tr"):
                    label = row.find("th", recursive=False)
                    cell = row.find("td", recursive=False)
                    if (
                        label
                        and cell
                        and "wikidatainfobox-lcell" in label.get_attribute_list("class")
                        and english(cell)
                    ):
                        facts.append(
                            Fact(
                                english(label),
                                english(cell),
                                url,
                                qualifier="Rendered Commons Wikidata infobox; source qualifications retained",
                            )
                        )
        else:
            for label in parser.select(".fileinfo-paramfield"):
                cell = label.find_next_sibling("td")
                if cell and text(clean(cell)):
                    field = str(label.get("id", "")).removeprefix("fileinfotpl_")
                    facts.append(
                        Fact(
                            "File " + text(clean(label)),
                            text(clean(cell)),
                            url,
                            qualifier="Media description metadata, not equipment specifications",
                        )
                    )
                    if field == "desc":
                        search.append(english(cell))
            if not any(search):
                search.extend(introduction(parser))
            media_licenses = sorted(
                {
                    text(node)
                    for node in parser.select(".licensetpl_short, .licensetpl_link")
                    if text(node)
                }
            )
            if media_licenses:
                facts.append(
                    Fact(
                        "Media license",
                        "; ".join(media_licenses),
                        url,
                        qualifier="Applies to linked media; page text license is separate",
                    )
                )
        categories = sorted({text(a) for a in soup.select("#mw-normal-catlinks li a")})
        facts.extend(
            Fact(
                "Commons category",
                name,
                url,
                qualifier="Page categorization, not an origin or operator assertion",
            )
            for name in categories
        )
        content = clean(content)
        for node in content.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    target = urljoin(canonical, str(node[attr]))
                    if urlsplit(target).scheme in {"http", "https"}:
                        node[attr] = target
                    else:
                        del node[attr]
        markdown = markdownify(str(content), heading_style="ATX").strip()
        if not markdown:
            # Empty named categories still provide stable identity and membership evidence.
            markdown = f"Commons category: {title}\n\n" + "\n".join(categories)
        selected_text = "\n\n".join(
            dict.fromkeys(value for value in search if value.strip())
        )
        links = sorted({str(node["href"]) for node in content.select("a[href]")})
        return [
            Entity(
                key=owner,
                title=self.subjects[owner]["title"],
                kind=self.subjects[owner]["kind"],
                url=url if category and owner == key and "?" not in url else "",
                aliases=self.subjects[owner]["aliases"],
                categories=["Wikimedia Commons equipment category"],
                facts=facts
                + [
                    Fact(
                        "Equipment category",
                        self.subjects[owner]["url"],
                        url,
                        qualifier="Reviewed category membership; photographs may depict multiple objects",
                    )
                ],
                evidence=[
                    Evidence(
                        url=url,
                        title=title,
                        markdown=markdown,
                        language="mul",
                        links=links,
                        attribution=attribution,
                        search_text=f"{self.subjects[owner]['title']}\n{selected_text}".strip(),
                    )
                ],
            )
            for owner in sorted(owners)
        ]
