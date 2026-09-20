from collections.abc import Iterable
from pathlib import Path
from typing import Protocol, runtime_checkable

from pipelines.model import Entity


class Source(Protocol):
    id: str
    version: str
    seeds: tuple[str, ...]
    minimum_entities: int

    def normalize(self, url: str) -> str | None: ...

    def discover(self, url: str, body: bytes) -> list[str]: ...

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]: ...

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]: ...


@runtime_checkable
class SupplementalDiscovery(Protocol):
    def discovery_seeds(self, directory: Path) -> list[str]: ...


@runtime_checkable
class PreparedSource(Protocol):
    def prepare(self, pages: Iterable[tuple[str, bytes]]) -> None: ...
