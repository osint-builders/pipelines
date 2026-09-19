from typing import Protocol

from pipelines.model import Document


class Source(Protocol):
    id: str
    version: str
    seeds: tuple[str, ...]
    minimum_documents: int

    def normalize(self, url: str) -> str | None: ...

    def discover(self, url: str, body: bytes) -> list[str]: ...

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]: ...

    def extract(self, url: str, body: bytes, names: list[str]) -> Document | None: ...
