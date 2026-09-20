"""Military Periscope's authenticated trial catalog; extraction is entirely offline."""

import json
import os
import re
from collections.abc import Iterable
from html import escape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit

from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.militaryperiscope_content import document, render

ORIGIN = "https://militaryperiscope.com"
API = ORIGIN + "/wt/api/nextjs/v1/page_by_path/"
TOC = ORIGIN + "/wt/api/nextjs/v1/trial_content/"
TRIAL = ORIGIN + "/trial-access/"
ROOTS = {"weapons", "armedforces", "defense-companies", "militant-organizations"}
SUBJECTS = {
    "WeaponNamePage",
    "CountriesPage",
    "FIReportCompanyPage",
    "MilitaryOrganizationPage",
}
# Publisher radar categories also contain optical sensors. These native IDs were
# checked against their descriptions; retain the publisher categories separately.
SENSOR_IDS = {"406462", "324884", "406350", "333806", "333021"}


def entity_kind(node: dict) -> EntityKind:
    if node["page_type"] != "WeaponNamePage":
        return EntityKind.ITEM
    categories = set(node["categories"])
    if str(node["id"]) in SENSOR_IDS:
        return EntityKind.SENSOR
    if categories & {"Torpedoes", "Mines"}:
        return EntityKind.WEAPON
    if categories & {"Unmanned Aerial Vehicles", "Aircraft"}:
        return EntityKind.AIRCRAFT
    if categories & {"Ground Combat Vehicles", "Unmanned Ground Vehicles"}:
        return EntityKind.VEHICLE
    if categories & {
        "Remotely Operated Vehicles",
        "Unmanned Underwater Vehicles",
        "Unmanned Surface Vessels",
        "Naval Warfare",
    }:
        return EntityKind.VESSEL
    if categories & {"Airborne Radars", "Ground Radars", "Naval Radars"}:
        return EntityKind.RADAR
    if categories & {"Electronics", "Space", "Unmanned"}:
        return EntityKind.EQUIPMENT
    return EntityKind.WEAPON


COLLECTIONS = {
    "WeaponsPage",
    "WeaponsCategoryPage",
    "ArmedForcesListPage",
    "GeographicRegionsPage",
    "DefenseCompanyListPage",
    "MilitaryOrganizationListPage",
    "RegionPage",
}


def canonical_path(value: str) -> str | None:
    parts = urlsplit(value)
    if parts.netloc and parts.netloc not in {
        "militaryperiscope.com",
        "www.militaryperiscope.com",
    }:
        return None
    if parts.scheme and parts.scheme not in {"https", "http"}:
        return None
    path = parts.path
    if (
        parts.query
        or parts.fragment
        or not re.fullmatch(r"/(?:[A-Za-z0-9_-]+/)+", path)
    ):
        return None
    return path if path.split("/")[1] in ROOTS else None


def page_url(path: str) -> str:
    if canonical_path(path) != path:
        raise ValueError("Invalid Military Periscope content path")
    return API + "?" + urlencode({"html_path": path})


def payload(body: bytes, *, allow_restricted: bool = False) -> tuple[str, dict]:
    data = json.loads(body)
    props = data.get("component_props")
    if not isinstance(props, dict) or not isinstance(data.get("component_name"), str):
        raise ValueError("Missing Military Periscope page payload")
    if type(props.get("restricted")) is not bool:
        raise ValueError("Missing Military Periscope access status")
    if props["restricted"] and not allow_restricted:
        raise PermissionError("Military Periscope session expired or page restricted")
    if type(props.get("id")) is not int or props["id"] <= 0 or not props.get("title"):
        raise ValueError("Missing Military Periscope page identity")
    return data["component_name"], props


def catalog(body: bytes) -> dict[str, dict]:
    data = json.loads(body)
    sections = data.get("sections")
    if not isinstance(sections, list) or not sections:
        raise PermissionError("Military Periscope trial catalog unavailable")
    nodes: dict[str, dict] = {}

    def visit(items: list, parents: list[str]) -> None:
        for node in items:
            if (
                not isinstance(node, dict)
                or type(node.get("id")) is not int
                or not isinstance(node.get("title"), str)
                or not node["title"].strip()
            ):
                raise ValueError("Invalid Military Periscope catalog node")
            path = canonical_path(node.get("url", ""))
            kind = node.get("page_type")
            children = node.get("children")
            if (
                path is None
                or kind not in SUBJECTS | COLLECTIONS
                or not isinstance(children, list)
            ):
                raise ValueError("Unsupported Military Periscope catalog node")
            if (kind in SUBJECTS and children) or (
                kind in COLLECTIONS and not children
            ):
                raise ValueError("Unexpected Military Periscope catalog hierarchy")
            item = {
                "id": node["id"],
                "title": node["title"],
                "page_type": kind,
                "categories": parents,
            }
            if path in nodes:
                previous = nodes[path]
                if any(previous[k] != item[k] for k in ("id", "title", "page_type")):
                    raise ValueError("Conflicting Military Periscope catalog identity")
                item["categories"] = sorted(set(previous["categories"] + parents))
            nodes[path] = item
            visit(children, parents + [node["title"]])

    visit(sections, [])
    if {urlsplit(n["url"]).path.strip("/") for n in sections} != ROOTS:
        raise ValueError("Military Periscope trial sections changed")
    identities: dict[int, str] = {}
    for path, node in nodes.items():
        if node["id"] in identities and identities[node["id"]] != path:
            raise ValueError("Military Periscope native ID has conflicting paths")
        identities[node["id"]] = path
    return nodes


class MilitaryPeriscope:
    id = "militaryperiscope"
    version = "1"
    seeds: tuple[str, ...] = (TOC, TRIAL)
    minimum_entities = 143

    def __init__(self) -> None:
        self.subjects: dict[str, dict] = {}
        self.nodes: dict[str, dict] = {}
        self.restricted: set[str] = set()

    def normalize(self, url: str) -> str | None:
        if url in self.seeds:
            return url
        parts = urlsplit(url)
        if (
            parts.scheme != "https"
            or parts.netloc != "militaryperiscope.com"
            or parts.path != urlsplit(API).path
            or parts.fragment
        ):
            return None
        pairs = parse_qsl(parts.query, keep_blank_values=True)
        if (
            len(pairs) != 1
            or pairs[0][0] != "html_path"
            or canonical_path(pairs[0][1]) != pairs[0][1]
        ):
            return None
        return page_url(pairs[0][1])

    def request_headers(self, url: str) -> dict[str, str]:
        if self.normalize(url) != url:
            raise ValueError("Credentials requested outside Military Periscope scope")
        cookie = os.environ.get("MILITARYPERISCOPE_COOKIE", "").strip()
        filename = os.environ.get("MILITARYPERISCOPE_COOKIE_FILE")
        if not cookie and filename:
            cookie = Path(filename).read_text(encoding="utf-8-sig").strip()
        if (
            not cookie
            or "\n" in cookie
            or "\r" in cookie
            or not re.search(r"(?:^|;\s*)sessionid=[^;\s]+", cookie)
        ):
            raise ValueError(
                "Set MILITARYPERISCOPE_COOKIE_FILE or MILITARYPERISCOPE_COOKIE to the trial session cookie"
            )
        return {
            "Cookie": cookie,
            "Accept": "application/json" if url != TRIAL else "text/html",
        }

    def discover(self, url: str, body: bytes) -> list[str]:
        if self.normalize(url) != url:
            raise ValueError("Outside Military Periscope scope")
        if url == TOC:
            return sorted(page_url(path) for path in catalog(body))
        if url == TRIAL:
            if b"__NEXT_DATA__" not in body or b"Redistribution" not in body:
                raise ValueError("Missing Military Periscope trial page/attribution")
            return []
        kind, props = payload(body, allow_restricted=True)
        if props["restricted"]:
            if props.get("restriction_type") == "login":
                return []  # Record the subscription-only response for the coverage audit.
            raise PermissionError("Military Periscope trial access expired")
        if kind in COLLECTIONS:
            return []  # The trial catalog is the authoritative accessible subset.
        path = dict(parse_qsl(urlsplit(url).query))["html_path"]
        base = (
            path.rsplit("/", 2)[0] + "/"
            if path.startswith(("/weapons/", "/armedforces/"))
            else path
        )
        result = set()
        for link in props.get("related_url", []):
            target = canonical_path(link.get("url", ""))
            if target is None or not target.startswith(base):
                raise ValueError("Unexpected Military Periscope related section")
            result.add(page_url(target))
        return sorted(result)

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        if url == TOC:
            return {
                page_url(path): [node["title"]] for path, node in catalog(body).items()
            }
        return {}

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        saved = dict(pages)
        self.nodes = catalog(saved[TOC])
        self.subjects = {}
        self.restricted = set()
        for path, node in self.nodes.items():
            url = page_url(path)
            if url not in saved:
                raise ValueError("Incomplete Military Periscope trial catalog capture")
            if node["page_type"] in SUBJECTS:
                pending = [url]
                while pending:
                    current = pending.pop()
                    if current in self.subjects:
                        if self.subjects[current]["id"] != node["id"]:
                            raise ValueError(
                                "Conflicting Military Periscope section ownership"
                            )
                        continue
                    if current not in saved:
                        raise ValueError(
                            "Incomplete Military Periscope detail sections"
                        )
                    _, props = payload(saved[current], allow_restricted=current != url)
                    if props["restricted"]:
                        if props.get("restriction_type") != "login":
                            raise PermissionError(
                                "Military Periscope trial access expired"
                            )
                        self.restricted.add(current)
                    self.subjects[current] = node
                    pending.extend(self.discover(current, saved[current]))

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if self.normalize(url) != url:
            raise ValueError("Outside Military Periscope scope")
        if not self.nodes:
            raise ValueError("Military Periscope extraction requires the trial catalog")
        if url not in self.subjects:
            return []
        if url in self.restricted:
            return []
        node = self.subjects[url]
        component, props = payload(body)
        expected = {
            "WeaponNamePage": "WeaponDetailPage",
            "CountriesPage": "FIReportPage",
            "FIReportCompanyPage": "FIReportPage",
            "MilitaryOrganizationPage": "MilitaryOrganizationPage",
        }
        if component != expected[node["page_type"]]:
            raise ValueError("Unexpected Military Periscope detail component")
        path = dict(parse_qsl(urlsplit(url).query))["html_path"]
        canonical = ORIGIN + path
        if canonical_path(props.get("seo", {}).get("seo_og_url", "")) != path:
            raise ValueError(
                "Military Periscope response does not match requested page"
            )
        if node["page_type"] in {"FIReportCompanyPage", "MilitaryOrganizationPage"}:
            if props["id"] != node["id"] or props["title"] != node["title"]:
                raise ValueError("Military Periscope subject identity mismatch")
        else:
            parents = props.get("parent_items", [])
            if (
                not parents
                or parents[-1].get("title") != node["title"]
                or parents[-1].get("url") != path.rsplit("/", 2)[0] + "/"
            ):
                raise ValueError("Military Periscope section parent mismatch")
        blocks = (
            props.get("section")
            if component == "WeaponDetailPage"
            else props.get("content")
        )
        if not isinstance(blocks, list) or not blocks:
            raise ValueError("Missing Military Periscope full content")
        facts = [
            Fact("Native subject ID", str(node["id"]), canonical),
            Fact("Native page ID", str(props["id"]), canonical),
        ]
        for key in (
            "last_published_at",
            "major_update_date",
            "minor_update_date",
            "first_published_at",
            "is_archived",
            "restriction_type",
            "status_notes",
        ):
            if key in props and props[key] not in (None, ""):
                facts.append(
                    Fact(key.replace("_", " ").title(), str(props[key]), canonical)
                )
        for country in props.get("country", []):
            facts.append(Fact("Country", country["title"], canonical))
        fragment = render(blocks, props.get("readable_name"))
        title = " ".join(node["title"].split())
        page_title = (
            title if props["title"] == title else title + " — " + props["title"]
        )
        metadata = (
            "<dl>"
            + "".join(
                f"<dt>{escape(f.name)}</dt><dd>{escape(f.raw)}</dd>" for f in facts
            )
            + "</dl>"
        )
        soup = document(
            f"<h1>{escape(page_title)}</h1>{metadata}{fragment}", canonical, page_title
        )
        content = soup.body
        assert content is not None
        markdown = markdownify(str(content), heading_style="ATX").strip()
        links = sorted({str(a["href"]) for a in content.select("a[href]")})
        # Preserve image credits in evidence, but don't embed asset filenames.
        focused = document(fragment, canonical, page_title)
        for figure in focused.select("figure"):
            figure.decompose()
        search_text = (
            page_title
            + "\n"
            + markdownify(str(focused.body), heading_style="ATX").strip()
        )
        if len(search_text) < 80:
            raise ValueError("Military Periscope detail content is unexpectedly short")
        categories = node["categories"]
        entity = Entity(
            key=str(node["id"]),
            title=title,
            kind=entity_kind(node),
            categories=sorted(set(categories)),
            facts=facts,
            url=canonical if path in self.nodes else "",
            evidence=[
                Evidence(
                    url=url,
                    title=page_title,
                    canonical_url=canonical,
                    markdown=markdown,
                    rendered_html=str(soup),
                    search_text=search_text,
                    links=links,
                    attribution="Military Periscope (GovExec). All rights reserved. Redistribution of the content is prohibited without prior consent of Military Periscope. Captured through the authorized trial API for local consumption. Original JSON and publisher update dates retained; historical/archived material does not establish current capability. Media remain links with their source credits. HTML and Markdown are rendered from publisher content blocks; full API response is retained separately.",
                )
            ],
        )
        entity.validate()
        return [entity]
