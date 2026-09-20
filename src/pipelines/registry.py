import importlib
import tomllib
from pathlib import Path
from typing import cast

from pipelines.sources.base import Source


def source_names() -> list[str]:
    return sorted(tomllib.loads(Path(__file__).with_name("sources.toml").read_text()))


def get_source(name: str) -> Source:
    catalog = Path(__file__).resolve().parent / "sources.toml"
    entries = tomllib.loads(catalog.read_text(encoding="utf-8"))
    if name not in entries:
        raise ValueError(f"Unknown source: {name}")
    module, symbol = entries[name]["adapter"].split(":")
    source = cast(Source, getattr(importlib.import_module(module), symbol)())
    if source.id != name:
        raise ValueError("Source ID must match its registry entry")
    return source
