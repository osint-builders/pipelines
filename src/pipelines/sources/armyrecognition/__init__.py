import json
import re
from pathlib import Path
from urllib.parse import parse_qsl, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Comment, Tag
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact, evidence_id
from pipelines.sources.html import text

ORIGIN = "https://www.armyrecognition.com"
CATALOG = "/military-products/army/radars/air-defense-radars"
SEED = ORIGIN + CATALOG
SUBJECTS = json.loads(Path(__file__).with_name("subjects.json").read_text())
TERMS = ORIGIN + "/legal-information"


def clean(content: Tag) -> None:
    for node in content.select(".section-title nav"):
        node.name = "div"
    for node in content.select(
        "script, style, form, iframe, nav, noscript, .ezoic-autoinsert-ad, "
        "[id^=ezoic-], [data-ez-ph-id], .sharethis-inline-share-buttons, "
        ".sigFreeClear, a[href^='#'], [hidden], [aria-hidden=true], "
        ".armypub, .bannergroup, .banneritem, a[href*='/component/banners/']"
    ):
        node.decompose()
    for comment in content.find_all(string=lambda s: isinstance(s, Comment)):
        comment.extract()
    for node in content.select(".el-title, span[style]"):
        if text(node) == "Back to top" or (
            text(node) == "a" and "#e1f7e3" in str(node.get("style", "")).lower()
        ):
            node.decompose()


def legacy_specs(content: Tag) -> list[tuple[str, str]]:
    """Pair adjacent colored rows by column before flattening layout tables."""
    facts = []
    for table in list(content.select("table")):
        if table.select("table"):
            continue
        rows = table.select("tr")
        pairs: list[tuple[Tag, Tag]] = []
        for index, row in enumerate(rows):
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells or any(
                str(cell.get("bgcolor", row.get("bgcolor", ""))).lower() != "#bfe9c2"
                for cell in cells
            ):
                continue
            values = (
                rows[index + 1].find_all(["td", "th"], recursive=False)
                if index + 1 < len(rows)
                else []
            )
            if not values or any(
                str(cell.get("bgcolor", rows[index + 1].get("bgcolor", ""))).lower()
                != "#e1f7e3"
                for cell in values
            ):
                continue
            if len(cells) != len(values):
                raise ValueError("Army Recognition specification columns changed")
            pairs.extend(zip(cells, values, strict=True))
        if not pairs:
            continue
        fragment = BeautifulSoup("", "html.parser")
        for label, value in pairs:
            name, raw = text(label), text(value)
            if not name or not raw:
                raise ValueError("Empty Army Recognition specification")
            facts.append((name, raw))
            p = fragment.new_tag("p")
            strong = fragment.new_tag("strong")
            strong.string = name + ": "
            p.append(strong)
            for child in list(value.contents):
                p.append(child.extract())
            label.clear()
            label.append(p)
    for node in content.select("table, thead, tbody, tr, td, th"):
        node.name = "div"
    return facts


class ArmyRecognition:
    id = "armyrecognition"
    version = "1"
    seeds: tuple[str, ...] = (SEED,)
    minimum_entities = 11
    subjects = SUBJECTS

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in {"www.armyrecognition.com", "armyrecognition.com"}
            or parts.port not in {None, 80, 443}
            or parts.username is not None
        ):
            return None
        path = parts.path.rstrip("/")
        # These exact Joomla parameters select category 139, not fighter aircraft.
        if path == "/military-products/air/fighter" and sorted(
            parse_qsl(parts.query, keep_blank_values=True)
        ) == [("id", "139"), ("task", "view")]:
            return SEED
        if parts.query or (
            path != CATALOG
            and not re.fullmatch(
                re.escape(CATALOG) + r"/[a-z0-9]+(?:-[a-z0-9]+)*", path
            )
        ):
            return None
        return urlunsplit(("https", "www.armyrecognition.com", path, "", ""))

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        if url != SEED:
            return {}
        soup = BeautifulSoup(body, "html.parser")
        heading = soup.select_one("main h1")
        if heading is None or text(heading).rstrip(" .") != "Air Defense Radars":
            raise ValueError("Army Recognition category identity changed")
        main = soup.select_one("main")
        assert main is not None
        for link in main.select("a[href]"):
            target = urljoin(url, str(link["href"]))
            if urlsplit(target).query or link.find_parent(class_="uk-pagination"):
                raise ValueError(
                    "Army Recognition catalog pagination needs review; robots disallows start"
                )
        members = {}
        for link in main.select("h3.el-title a[href]"):
            member = self.normalize(urljoin(url, str(link["href"])))
            if member is None or member == SEED or not text(link):
                raise ValueError(
                    "Army Recognition catalog member is outside reviewed scope"
                )
            members[member] = [text(link)]
        if not members:
            raise ValueError("Army Recognition returned no catalog members")
        return members

    def discover(self, url: str, body: bytes) -> list[str]:
        return sorted(self.labels(url, body))

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if url == SEED:
            self.labels(url, body)
            return []
        if not names:
            raise ValueError("Army Recognition article has no catalog membership")
        soup = BeautifulSoup(body, "html.parser")
        canonical = soup.select_one('link[rel="canonical"]')
        target = self.normalize(str(canonical.get("href", ""))) if canonical else None
        if target != url:
            raise ValueError(
                "Army Recognition canonical article changed; review identity"
            )
        slug = urlsplit(url).path.rsplit("/", 1)[-1]
        if slug not in self.subjects:
            raise ValueError(f"Unreviewed Army Recognition subject: {slug}")
        heading = soup.select_one("main h1")
        title = text(heading).rstrip(" .") if heading else ""
        if not title or title not in names:
            raise ValueError("Army Recognition article title differs from catalog")
        content_soup = BeautifulSoup("<div></div>", "html.parser")
        content = content_soup.div
        assert content is not None
        legacy = soup.select_one("main .content-article-template")
        if legacy is not None:
            metadata = heading.find_next_sibling("div") if heading else None
            if metadata is not None:
                content.append(metadata.extract())
            content.append(legacy.extract())
            clean(content)
            specs = legacy_specs(content)
        else:
            sections = soup.select("main #desc, main #data, main #spec")
            if len(sections) != 3 or any(len(text(node)) < 30 for node in sections):
                raise ValueError("Incomplete Army Recognition article sections")
            for node in soup.select(
                "main .section-title, main .uk-padding-remove-top, "
                "main #desc, main #data, main #spec, main #details, main #photos"
            ):
                content.append(node.extract())
            clean(content)
            specs = []
            for item in content.select("#spec li.el-item"):
                label, value = (
                    item.select_one(".el-title"),
                    item.select_one(".el-content"),
                )
                if label is None or value is None or not text(label) or not text(value):
                    raise ValueError("Incomplete Army Recognition specification")
                specs.append((text(label), text(value)))
        if len(specs) < 3 or len(text(content)) < 200:
            raise ValueError("Incomplete Army Recognition equipment article")
        # Keep original photos as links; omit transient thumbnail-cache URLs.
        for img in content.select("img.sigFreeImg"):
            parent = img.find_parent("a", href=True)
            if parent:
                img["src"] = parent["href"]
        for node in content.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    link = urljoin(url, str(node[attr])).replace(" ", "%20")
                    if urlsplit(link).scheme in {"http", "https"}:
                        node[attr] = link
                    else:
                        del node[attr]
        links = sorted(
            {
                str(node[attr])
                for attr in ("href", "src")
                for node in content.select(f"[{attr}]")
            }
        )
        # Text nodes exclude repetitive image filenames without losing article prose.
        search_text = content.get_text("\n", strip=True)
        subject = self.subjects[slug]
        aliases = [
            alias
            for alias in subject["aliases"]
            if re.search(
                r"(?<![\w-])" + re.escape(alias) + r"(?![\w-])",
                title + "\n" + search_text,
                re.I,
            )
        ]
        markdown = markdownify(str(content), heading_style="ATX").strip()
        return [
            Entity(
                key=evidence_id(url),
                title=title,
                kind=EntityKind(subject["kind"]),
                aliases=sorted(set(aliases + [title])),
                categories=["Air Defense Radars"],
                facts=[
                    Fact(
                        name,
                        raw,
                        url,
                        qualifier="As stated by Army Recognition; unverified source claim",
                    )
                    for name, raw in specs
                ],
                evidence=[
                    Evidence(
                        url=url,
                        title=title,
                        markdown=markdown,
                        search_text=search_text,
                        links=links,
                        attribution=f"Copyright Army Recognition Group and its licensors. Source: {url}. Converted to Markdown. Reuse terms: {TERMS}; no redistribution license is granted by this scraper.",
                    )
                ],
            )
        ]
