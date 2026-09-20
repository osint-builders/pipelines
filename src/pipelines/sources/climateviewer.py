"""Historical Fortress Russia site markers from a bounded GeoJSON layer."""

import hashlib
import json
import math
import re
from collections.abc import Iterable
from html import escape
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from markdownify import markdownify

from pipelines.model import Entity, EntityKind, Evidence, Fact

ORIGIN = "https://climateviewer.org"
DATA = (
    ORIGIN + "/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson"
)
MAP = (
    ORIGIN
    + "/history-and-science/government/maps/fortress-russia-air-defence-radar-sam-sites/"
)
LICENSE = "https://creativecommons.org/licenses/by-nc-sa/4.0/"
CONTEXT = "Historical Fortress Russia map, based on a guide dated 2010, in a layer distributed under a 2018 path. Individual observation dates are not supplied for every marker; descriptions do not establish current status."


def coordinate(value: object) -> None:
    if (
        not isinstance(value, list)
        or len(value) not in {2, 3}
        or any(type(v) not in {int, float} or not math.isfinite(v) for v in value)
    ):
        raise ValueError("Invalid GeoJSON coordinate")
    if not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90:
        raise ValueError("GeoJSON coordinates outside longitude/latitude bounds")


def features(body: bytes) -> list[dict]:
    data = json.loads(body)
    if (
        not isinstance(data, dict)
        or data.get("type") != "FeatureCollection"
        or not isinstance(data.get("features"), list)
        or not data["features"]
    ):
        raise ValueError("Missing ClimateViewer FeatureCollection")
    if "crs" in data:
        raise ValueError("Unexpected GeoJSON coordinate reference system")
    result = data["features"]
    for feature in result:
        if (
            not isinstance(feature, dict)
            or feature.get("type") != "Feature"
            or not isinstance(feature.get("properties"), dict)
            or not isinstance(feature.get("geometry"), dict)
        ):
            raise ValueError("Invalid ClimateViewer feature")
        properties, geometry = feature["properties"], feature["geometry"]
        if (
            not isinstance(properties.get("name"), str)
            or not properties["name"].strip()
        ):
            raise ValueError("Missing ClimateViewer marker name")
        if geometry.get("type") == "Point":
            coordinate(geometry.get("coordinates"))
            if (
                not isinstance(properties.get("description"), str)
                or not properties["description"].strip()
            ):
                raise ValueError("Missing ClimateViewer site description")
        elif geometry.get("type") == "LineString":
            if (
                not isinstance(geometry.get("coordinates"), list)
                or len(geometry["coordinates"]) < 2
            ):
                raise ValueError("Invalid ClimateViewer overlay")
            for point in geometry["coordinates"]:
                coordinate(point)
        else:
            raise ValueError("Unsupported ClimateViewer geometry; review source scope")
    return result


def marker_key(feature: dict) -> str:
    identity = [
        feature["properties"]["name"],
        [float(x) for x in feature["geometry"]["coordinates"]],
    ]
    return (
        "fortress-russia-"
        + hashlib.sha256(
            json.dumps(identity, separators=(",", ":"), ensure_ascii=False).encode()
        ).hexdigest()[:24]
    )


def description(raw: str) -> tuple[str, str, list[str]]:
    # The converted KML uses closing br tags and one legacy Windows dash character.
    soup = BeautifulSoup(
        re.sub(r"</br\s*>", "<br>", raw.replace("\x96", "-"), flags=re.I), "html.parser"
    )
    for node in soup.select("script, style, form"):
        node.decompose()
    for node in soup.select("a[href], img[src]"):
        attribute = "href" if node.name == "a" else "src"
        node[attribute] = urljoin(DATA, str(node[attribute]))
    full = markdownify(str(soup), heading_style="ATX").strip()
    links = {
        str(node.get("href", node.get("src", "")))
        for node in soup.select("a[href], img[src]")
    }
    links.update(re.findall(r"https?://[^\s<>\"']+", soup.get_text(" ", strip=True)))
    for node in soup.select("img"):
        node.decompose()
    for node in soup.select("br"):
        node.replace_with("\n")
    lines = [
        " ".join(line.split())
        for line in soup.get_text(" ").splitlines()
        if line.strip()
    ]
    return (
        full,
        "\n".join(lines),
        sorted(link for link in links if urlsplit(link).scheme in {"http", "https"}),
    )


class ClimateViewer:
    id = "climateviewer"
    version = "1"
    seeds: tuple[str, ...] = (DATA, MAP)
    minimum_entities = 350

    def __init__(self) -> None:
        self.attribution = ""

    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if (
            parts.scheme not in {"http", "https"}
            or parts.netloc != "climateviewer.org"
            or parts.query
        ):
            return None
        canonical = ORIGIN + parts.path
        return canonical if canonical in self.seeds else None

    def discover(self, url: str, body: bytes) -> list[str]:
        if self.normalize(url) != url:
            raise ValueError("Outside ClimateViewer source scope")
        if url == DATA:
            features(body)
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None:
        self.attribution = ""
        saved = dict(pages)
        if not set(self.seeds).issubset(saved):
            raise ValueError("Incomplete ClimateViewer archive")
        soup = BeautifulSoup(saved[MAP], "html.parser")
        licenses = [
            a
            for a in soup.select('a[rel="license"][href]')
            if str(a["href"]).replace("http://", "https://") == LICENSE
        ]
        content = soup.select_one(".post-content")
        if (
            not licenses
            or content is None
            or DATA not in content.get_text(" ")
            or "Integrated Air Defence of Russia 2010" not in content.get_text(" ")
        ):
            raise ValueError("ClimateViewer map provenance or license changed")
        notice = (
            " ".join(licenses[0].parent.stripped_strings) if licenses[0].parent else ""
        )
        if "Jim Lee" not in notice or "Fortress Russia" not in notice:
            raise ValueError("Missing ClimateViewer contributor attribution")
        self.attribution = f"{notice}\n\nLicense: {LICENSE}\nMap and source context: {MAP}\n{CONTEXT}\nPoint feature retained in records; HTML and Markdown are generated from its properties. Original GeoJSON, including line overlays, is available through source export."
        features(saved[DATA])

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if self.normalize(url) != url:
            raise ValueError("Outside ClimateViewer source scope")
        if url == MAP:
            return []
        if not self.attribution:
            raise ValueError("ClimateViewer extraction requires prepared provenance")
        result = []
        seen = set()
        items = features(body)
        canonical_features = sorted(
            json.dumps(item, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            for item in items
        )
        layer_digest = hashlib.sha256(
            json.dumps(
                canonical_features, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        for feature in items:
            if feature["geometry"]["type"] != "Point":
                continue
            key = marker_key(feature)
            if key in seen:
                raise ValueError("Duplicate ClimateViewer marker identity")
            seen.add(key)
            properties = feature["properties"]
            native = properties["name"]
            full, plain, links = description(properties["description"])
            if not plain:
                raise ValueError("Empty ClimateViewer site description")
            lead = plain.splitlines()[0]
            if re.match(r"S-(?:200|300|400)_", native):
                category = "SAM sites"
            elif "A-135" in lead:
                category = "ABM sites"
            elif "base" in lead.lower():
                category = "Air bases"
            elif "site" in lead.lower():
                category = "Radar sites"
            else:
                raise ValueError("Unrecognized ClimateViewer site type; review scope")
            title = f"{native}: {lead}"
            longitude, latitude = feature["geometry"]["coordinates"][:2]
            facts = [
                Fact("Layer data fingerprint", layer_digest, DATA),
                Fact("Source marker name", native, DATA),
                Fact(
                    "Longitude", str(longitude), DATA, values=[longitude], unit="degree"
                ),
                Fact("Latitude", str(latitude), DATA, values=[latitude], unit="degree"),
                Fact("Historical context", CONTEXT, DATA),
            ]
            for line in plain.splitlines():
                if ":" in line and not line.startswith(("http:", "https:")):
                    label, value = line.split(":", 1)
                    if label.strip() and value.strip():
                        facts.append(Fact(label.strip(), value.strip(), DATA))
            record_json = json.dumps(
                feature, indent=2, ensure_ascii=False, sort_keys=True
            )
            markdown = f"{CONTEXT}\n\nCategory: {category}\n\nLongitude: {longitude}\nLatitude: {latitude}\n\n{full}\n\n## Original point feature\n\n```json\n{record_json}\n```"
            rendered = f'<!doctype html>\n<html lang="en"><head><meta charset="utf-8"><title>{escape(title)}</title></head><body><h1>{escape(title)}</h1><p>{escape(CONTEXT)}</p><p>Source: <a href="{DATA}">{DATA}</a></p><pre>{escape(self.attribution)}</pre><h2>Description</h2><pre>{escape(plain)}</pre><h2>Original point feature</h2><pre>{escape(record_json)}</pre></body></html>\n'
            search_lines = [
                line
                for line in plain.splitlines()
                if not line.lower().startswith(("credit", "http://", "https://"))
            ]
            result.append(
                Entity(
                    key=key,
                    title=title,
                    kind=EntityKind.SITE,
                    aliases=[native] if len(native) >= 4 else [],
                    categories=["Fortress Russia", "Historical map", category],
                    facts=facts,
                    url=DATA,
                    evidence=[
                        Evidence(
                            url=DATA,
                            title=title,
                            markdown=markdown,
                            attribution=self.attribution,
                            links=sorted(set(links + [MAP, LICENSE])),
                            rendered_html=rendered,
                            record_id=key,
                            records=[feature],
                            search_text=f"Historical Fortress Russia {category}. Marker {native}.\n"
                            + "\n".join(search_lines),
                        )
                    ],
                )
            )
        return result
