"""Deagel Armies: browser discovery, streamed HTML, and distinct equipment variants."""

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from urllib.robotparser import RobotFileParser

from bs4 import BeautifulSoup, Tag
from markdownify import markdownify

from pipelines.archive import atomic_json
from pipelines.model import Entity, EntityKind, Evidence, Fact

ORIGIN = "https://www.deagel.com"
DETAIL = re.compile(r"/Armies/.+/(a\d{6})$")
VARIANT = re.compile(r"\d{3}$")


def text(node: Tag) -> str:
    return " ".join(node.stripped_strings)


def content(body: bytes) -> Tag:
    soup = BeautifulSoup(body, "html.parser")
    # Streamed Blazor responses put the completed detail component in a template.
    for template in reversed(soup.select("template[blazor-component-id]")):
        parsed = BeautifulSoup(template.decode_contents(), "html.parser")
        heading = parsed.find("h1")
        if (
            heading
            and heading.parent
            and text(heading)
            and parsed.find("a", id=VARIANT)
        ):
            return heading.parent
    main = soup.find("main")
    if main:
        heading = main.find("h1")
        if heading and heading.parent and text(heading):
            return heading.parent
        if main.select(".guide"):
            return main
    raise ValueError("Deagel returned no completed catalog or equipment component")


def markdown(node: Tag) -> str:
    return markdownify(
        str(node), heading_style="ATX", sub_symbol="<sub>", sup_symbol="<sup>"
    ).strip()


def fields(section: Tag) -> dict[str, str]:
    lead = section.select_one("p.fst-italic")
    if lead is None:
        raise ValueError("Deagel variant has no equipment metadata")
    result = {}
    for line in re.split(r"<br\s*/?>", str(lead), flags=re.I):
        label, separator, value = text(BeautifulSoup(line, "html.parser")).partition(
            " : "
        )
        if separator:
            result[label] = value
    return result


def kind(group: str, description: str) -> EntityKind:
    if group == "Surveillance Vehicles":
        first = re.split(r"(?<=[.!?])\s", description, maxsplit=1)[0].lower()
        if re.search(r"\b(passive|acoustic|sensor)\b", first):
            return EntityKind.SENSOR
        if re.search(r"\bradar\b", first):
            return EntityKind.RADAR
        return EntityKind.SENSOR
    if group in {
        "Area Air Defenses",
        "Local Air Defenses",
        "Towed Howitzers",
        "Towed Mortars",
        "Rocket Artillery Systems",
        "Self-Propelled Howitzers",
    }:
        return EntityKind.WEAPON
    if group == "Electronic Warfare Vehicles":
        return EntityKind.EQUIPMENT
    return EntityKind.VEHICLE


class Deagel:
    id = "deagel"
    version = "1"
    seeds: tuple[str, ...] = (ORIGIN + "/Armies",)
    minimum_entities = 1200

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.hostname not in {"deagel.com", "www.deagel.com"}
            or parts.port not in {None, 80, 443}
        ):
            return None
        path = unquote(parts.path).rstrip("/")
        if any(part in {".", ".."} for part in path.split("/")):
            return None
        if path != "/Armies" and not DETAIL.fullmatch(path):
            return None
        return urlunsplit(("https", "www.deagel.com", quote(path, safe="/"), "", ""))

    def discover(self, url: str, body: bytes) -> list[str]:
        # Only the complete catalog determines membership; related links are evidence.
        if urlsplit(url).path.rstrip("/") != "/Armies":
            return []
        page = content(body)
        by_id: dict[str, str] = {}
        for anchor in page.select(".guide a[href]"):
            target = self.normalize(urljoin(ORIGIN + "/", str(anchor["href"])))
            if target and (match := DETAIL.fullmatch(unquote(urlsplit(target).path))):
                by_id.setdefault(match[1], target)
        if len(by_id) < 1:
            raise ValueError("Deagel catalog contains no equipment links")
        return sorted(by_id.values())

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def discovery_seeds(self, directory: Path) -> list[str]:
        saved = directory / "discovery"
        path, manifest = saved / "armies.html", saved / "manifest.json"
        if manifest.exists():
            digest = json.loads(manifest.read_text(encoding="utf-8"))["sha256"]
            if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
                raise ValueError("Deagel discovery snapshot checksum mismatch")
            return self.discover(self.seeds[0], path.read_bytes())
        agent = (
            "OSINTBuildersPipelines/0.1 (+https://github.com/osint-builders/pipelines)"
        )
        with urlopen(
            Request(ORIGIN + "/robots.txt", headers={"User-Agent": agent}), timeout=30
        ) as response:
            robots_body = response.read()
        robots = RobotFileParser()
        robots.parse(robots_body.decode("utf-8", errors="replace").splitlines())
        if not robots.can_fetch(agent, self.seeds[0]):
            raise RuntimeError("Deagel robots.txt disallows catalog discovery")
        executable = shutil.which("agent-browser")
        if executable is None:
            raise RuntimeError(
                "Deagel full discovery requires agent-browser; see README"
            )
        native = (
            Path(executable).parent
            / "node_modules/agent-browser/bin/agent-browser-win32-x64.exe"
        )
        if Path(executable).suffix.lower() == ".cmd" and native.is_file():
            executable = str(native)
        session = "pipelines-deagel-" + directory.name

        def browser(*args: str) -> str:
            # Daemon children can inherit pipes on Windows; files avoid waiting for EOF.
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                result = subprocess.run(
                    [executable, "--session", session, *args],
                    stdout=output,
                    stderr=errors,
                    timeout=60,
                )
                if result.returncode:
                    errors.seek(0)
                    raise RuntimeError(errors.read().decode("utf-8", errors="replace"))
                output.seek(0)
                return output.read().decode("utf-8")

        try:
            browser("open", self.seeds[0])
            browser("wait", "--load", "networkidle")
            browser("wait", "--fn", "document.querySelector('#status_7') !== null")
            browser("find", "placeholder", "Keyword", "fill", "Abrams")
            browser(
                "wait",
                "--fn",
                "document.querySelectorAll('.guide a').length < 10",
            )
            browser("find", "placeholder", "Keyword", "fill", "")
            browser(
                "wait", "--fn", "document.querySelectorAll('.guide a').length > 1100"
            )
            if (
                browser("eval", "Boolean(document.querySelector('#cs.show'))").strip()
                == "true"
            ):
                browser("find", "role", "button", "click", "--name", "Confirm")
                browser("wait", "--fn", "!document.querySelector('#cs.show')")
            browser("find", "first", "button.dropdown-toggle", "click")
            browser("check", "#status_4")
            browser("wait", "#status_7")
            browser("check", "#status_7")
            browser(
                "wait",
                "--fn",
                "Array.from(document.querySelectorAll('.guide a')).some(a => a.getAttribute('href').endsWith('/a000516#001'))",
            )
            html = "<main>" + browser("get", "html", "main") + "</main>"
            body = html.encode()
            selected = "Array.from(document.querySelectorAll('input[id^=status_]')).filter(x => x.checked).length === 4"
            if browser("eval", selected).strip() != "true":
                raise ValueError("Deagel all-status discovery did not complete")
            urls = self.discover(self.seeds[0], body)
            if len(urls) < 790:
                raise ValueError("Deagel all-status catalog unexpectedly incomplete")
            saved.mkdir(parents=True, exist_ok=True)
            path.write_bytes(body)
            atomic_json(
                manifest,
                {
                    "url": self.seeds[0],
                    "capture": "browser-rendered all statuses",
                    "retrieved_at": datetime.now(UTC).isoformat(),
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "pages": len(urls),
                    "statuses": ["active", "cancelled", "retired", "under-development"],
                    "catalog_entries": len(
                        BeautifulSoup(body, "html.parser").select(".guide a[href]")
                    ),
                },
            )
            return urls
        finally:
            browser("close")

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        match = DETAIL.fullmatch(unquote(urlsplit(url).path))
        if not match:
            return []
        page = content(body)
        family = page.find("h1")
        if family is None:
            raise ValueError("Missing Deagel family title")
        family_title = text(family)
        for node in page.select("script, style, nav"):
            node.decompose()
        for node in page.select("[href], [src]"):
            for attr in ("href", "src"):
                if attr in node.attrs:
                    absolute = urljoin(ORIGIN + "/", str(node[attr]))
                    if urlsplit(absolute).scheme in {"http", "https"}:
                        node[attr] = absolute
                    else:
                        del node[attr]
        anchors = page.find_all("a", id=VARIANT)
        if not anchors:
            raise ValueError("Deagel equipment page has no variants")
        evidence = Evidence(
            url,
            family_title,
            markdown(page),
            links=sorted({str(n["href"]) for n in page.select("a[href]")}),
            attribution="Deagel.com. Copyright 2003-2026; all rights reserved. Source claims are retained as published.",
        )
        entities = []
        for anchor in anchors:
            heading = anchor.find_next_sibling("h1")
            section = heading.find_next_sibling("div") if heading else None
            if heading is None or section is None:
                raise ValueError("Missing Deagel variant section")
            title = text(heading)
            metadata = fields(section)
            group = metadata.get("Group")
            if not title or not group:
                raise ValueError("Missing Deagel variant name or group")
            reference = url + "#" + str(anchor["id"])
            facts = [Fact(name, value, reference) for name, value in metadata.items()]
            for table in section.select("table"):
                table_heading = table.find_previous("h5")
                table_name = text(table_heading) if table_heading else "Table"
                for row in table.select("tbody tr"):
                    cells = row.find_all("td", recursive=False)
                    if len(cells) >= 2 and text(cells[0]):
                        facts.append(
                            Fact(
                                table_name + ": " + text(cells[0]),
                                " | ".join(text(cell) for cell in cells[1:]),
                                reference,
                            )
                        )
            lead = section.select_one("p.pe-3")
            description = text(lead) if lead else ""
            aliases = [
                title,
                *[
                    part.strip()
                    for part in metadata.get("Also Known As", "").split(",")
                    if part.strip()
                ],
            ]
            entities.append(
                Entity(
                    key=match[1] + "-" + str(anchor["id"]),
                    title=title,
                    kind=kind(group, description),
                    evidence=[
                        replace(
                            evidence,
                            search_text=f"# {title}\n\nSource: {reference}\n\n"
                            + markdown(section),
                        )
                    ],
                    aliases=sorted(set(aliases)),
                    categories=[group],
                    facts=facts,
                    url=reference,
                )
            )
        return entities
