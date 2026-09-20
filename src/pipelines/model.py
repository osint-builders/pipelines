import hashlib
import re
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from urllib.parse import urlsplit

SCHEMA_VERSION = 2


def evidence_id(url: str, record_id: str = "") -> str:
    identity = url + ("\n" + record_id if record_id else "")
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


def valid_key(value: str) -> bool:
    return re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) is not None


class EntityKind(StrEnum):
    RADAR = "radar"
    EMITTER = "emitter"
    SENSOR = "sensor"
    VEHICLE = "vehicle"
    AIRCRAFT = "aircraft"
    VESSEL = "vessel"
    WEAPON = "weapon"
    EQUIPMENT = "equipment"
    ITEM = "item"
    SITE = "site"
    SPACECRAFT = "spacecraft"


@dataclass
class Fact:
    name: str
    raw: str
    evidence: str
    values: list[float] = field(default_factory=list)
    unit: str | None = None
    qualifier: str | None = None


@dataclass
class Evidence:
    url: str
    title: str
    markdown: str
    language: str = "en"
    links: list[str] = field(default_factory=list)
    attribution: str = ""
    retrieved_at: str = ""
    html_sha256: str = ""
    search_text: str = ""
    canonical_url: str = ""
    rendered_html: str = ""
    record_id: str = ""
    records: list[dict] = field(default_factory=list)

    @property
    def id(self) -> str:
        return evidence_id(self.url, self.record_id)


@dataclass
class Entity:
    key: str
    title: str
    kind: EntityKind
    evidence: list[Evidence]
    aliases: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    url: str = ""

    def validate(self) -> None:
        if not valid_key(self.key) or not self.title.strip():
            raise ValueError("Entity requires a safe source-native key and a title")
        EntityKind(self.kind)
        if not self.evidence:
            raise ValueError("Entity requires source evidence")
        for page in self.evidence:
            if page.record_id and (
                not valid_key(page.record_id)
                or not page.records
                or not page.rendered_html
            ):
                raise ValueError(
                    "Record evidence requires a safe record ID, records, and rendered HTML"
                )
            if page.records and not page.record_id:
                raise ValueError("Structured records require a record ID")
            if (
                urlsplit(page.url).scheme not in {"https", "http"}
                or not page.markdown.strip()
            ):
                raise ValueError("Entity evidence requires a URL and full Markdown")
        if len({page.id for page in self.evidence}) != len(self.evidence):
            raise ValueError("Duplicate evidence page")
        urls = {page.url for page in self.evidence}
        for page in self.evidence:
            if page.canonical_url:
                if urlsplit(page.canonical_url).scheme not in {"https", "http"}:
                    raise ValueError("Invalid canonical evidence URL")
                urls.add(page.canonical_url)
        if self.url and self.url.split("#")[0] not in urls:
            raise ValueError("Entity URL must reference retained evidence")
        if any(fact.evidence.split("#")[0] not in urls for fact in self.facts):
            raise ValueError("Entity facts must reference retained evidence")

    def metadata(self, source: str) -> dict:
        self.validate()
        value = asdict(self)
        value.pop("key")
        value.update(id=f"{source}:{self.key}", source=source, source_id=self.key)
        value["url"] = self.url or self.evidence[0].url
        for page, data in zip(self.evidence, value["evidence"], strict=True):
            data["id"] = page.id
            if not page.search_text:
                data.pop("search_text")
            if not page.canonical_url:
                data.pop("canonical_url")
            if not page.rendered_html:
                data.pop("rendered_html")
            if not page.record_id:
                data.pop("record_id")
                data.pop("records")
        return value

    def merge(self, other: "Entity") -> None:
        if (self.key, self.kind, self.title) != (other.key, other.kind, other.title):
            raise ValueError(f"Conflicting entity identity: {self.key}")
        self.aliases = sorted(set(self.aliases + other.aliases))
        self.categories = sorted(set(self.categories + other.categories))
        if not self.url and other.url:
            self.url = other.url
        known = {page.id: page for page in self.evidence}
        for page in other.evidence:
            if page.id in known and known[page.id] != page:
                raise ValueError(f"Conflicting evidence for entity: {self.key}")
            known[page.id] = page
        self.evidence = sorted(known.values(), key=lambda page: page.url)
        for fact in other.facts:
            if fact not in self.facts:
                self.facts.append(fact)
        self.validate()
