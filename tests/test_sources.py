"""A second, independent adapter exercises the shared entity pipeline."""

import json
from pathlib import Path

import pytest
from test_pipeline import archived

from pipelines.archive import Archive
from pipelines.build import publish
from pipelines.distribution import collect_artifacts, content_digest
from pipelines.model import Entity, EntityKind, Evidence, Fact
from pipelines.sources.base import Source


class CatalogSource:
    id = "catalog"
    version = "1"
    seeds: tuple[str, ...] = ("https://catalog.example/items",)
    minimum_entities = 2

    def normalize(self, url: str) -> str | None:
        return url if url.startswith("https://catalog.example/") else None

    def discover(self, url: str, body: bytes) -> list[str]:
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        items = json.loads(body)
        return [
            Entity(
                key=item["key"],
                title=item["name"],
                kind=EntityKind(item["kind"]),
                aliases=[item["name"], *names],
                evidence=[Evidence(url, "Equipment catalog", body.decode())],
                facts=[Fact("description", item["description"], url)],
            )
            for item in items
        ]


def test_second_adapter_catalog_and_multiple_evidence_pages(tmp_path: Path) -> None:
    source: Source = CatalogSource()
    directory = tmp_path / source.id
    vehicle = {
        "key": "vehicle-42",
        "name": "ASR 12",
        "kind": "vehicle",
        "description": "Tracked carrier",
    }
    emitter = {
        "key": "emitter-1",
        "name": "RF transmitter",
        "kind": "emitter",
        "description": "Vehicle mounted transmitter",
    }
    archive = Archive(directory / "archives" / "fixture")
    try:
        for path, records in [("items", [vehicle, emitter]), ("manual", [vehicle])]:
            url = f"https://catalog.example/{path}"
            archive.add(url)
            archive.save(url, 200, json.dumps(records).encode(), "text/html", {})
        archive.mark_complete(source.id)
        publish(source, archive, directory)
    finally:
        archive.close()
    radar, radar_archive, radar_dir = archived(tmp_path)
    try:
        publish(radar, radar_archive, radar_dir)
    finally:
        radar_archive.close()
    entities, html, _ = collect_artifacts(tmp_path, [radar.id, source.id])
    assert len(entities) == 3
    assert len(html) == 3
    by_id = {entity["id"]: entity for entity in entities}
    carrier = by_id["catalog:vehicle-42"]
    transmitter = by_id["catalog:emitter-1"]
    assert len(carrier["evidence"]) == 2
    assert len(transmitter["evidence"]) == 1
    assert carrier["evidence"][0]["id"] == transmitter["evidence"][0]["id"]
    assert sum(entity["title"] == "ASR 12" for entity in entities) == 2
    assert {entity["kind"] for entity in entities} == {"vehicle", "emitter", "radar"}
    before = content_digest(entities)
    carrier["evidence"][0]["retrieved_at"] = "later"
    assert content_digest(entities) == before
    carrier["evidence"][0]["markdown"] += " New specification."
    assert content_digest(entities) != before


def test_entity_contract_rejects_conflicting_identity_and_unretained_facts() -> None:
    entity = Entity(
        "vehicle-42",
        "Carrier",
        EntityKind.VEHICLE,
        [Evidence("https://example.com/item", "Carrier", "Full page")],
    )
    other = Entity(
        "vehicle-42",
        "Different item",
        EntityKind.EMITTER,
        [Evidence("https://example.com/other", "Other", "Full page")],
    )
    with pytest.raises(ValueError, match="Conflicting entity identity"):
        entity.merge(other)
    entity.facts = [Fact("frequency", "1 GHz", "https://unrelated.example/item")]
    with pytest.raises(ValueError, match="retained evidence"):
        entity.validate()
    entity.key = "../private"
    with pytest.raises(ValueError, match="safe source-native key"):
        entity.validate()
