import hashlib
import json
from pathlib import Path

from pipelines.archive import atomic_json
from pipelines.snapshot import load_snapshot
from pipelines.sources.base import Source


def previous_urls(source: Source, directory: Path) -> list[str]:
    cache = directory / "discovery/previous-urls.json"
    if cache.is_file():
        record = json.loads(cache.read_text(encoding="utf-8"))
        urls = record["urls"]
        if hashlib.sha256(json.dumps(urls).encode()).hexdigest() != record["sha256"]:
            raise ValueError("Previous feed membership checksum mismatch")
    else:
        source_dir = directory.parent.parent
        urls = []
        if (source_dir / "published/current.json").is_file():
            _, entities = load_snapshot(source_dir)
            urls = sorted({page["url"] for e in entities for page in e["evidence"]})
        cache.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(
            cache,
            {
                "urls": urls,
                "sha256": hashlib.sha256(json.dumps(urls).encode()).hexdigest(),
            },
        )
    if not isinstance(urls, list) or any(
        not isinstance(url, str) or source.normalize(url) != url or url in source.seeds
        for url in urls
    ):
        raise ValueError("Invalid previous feed membership")
    return urls
