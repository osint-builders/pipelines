import hashlib
from dataclasses import asdict, dataclass, field

SCHEMA_VERSION = 1


def document_id(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:24]


@dataclass
class Fact:
    name: str
    raw: str
    evidence: str
    values: list[float] = field(default_factory=list)
    unit: str | None = None
    qualifier: str | None = None


@dataclass
class Document:
    url: str
    title: str
    markdown: str
    kind: str = "article"
    language: str = "en"
    names: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    facts: list[Fact] = field(default_factory=list)
    links: list[str] = field(default_factory=list)
    attribution: str = ""
    retrieved_at: str = ""
    html_sha256: str = ""

    @property
    def id(self) -> str:
        return document_id(self.url)

    def metadata(self) -> dict:
        return {"id": self.id, **asdict(self)}
