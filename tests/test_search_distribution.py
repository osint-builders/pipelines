import copy
import json
import struct
import zipfile
from pathlib import Path

import pytest
from test_pipeline import archived

from pipelines import distribution, search_distribution
from pipelines.build import publish
from pipelines.distribution import canonical, collect_artifacts, sha256, write_bundle
from pipelines.media import MediaCandidate, MediaReference, MediaStore, media_id
from pipelines.search_distribution import (
    CAPTIONS_MEMBER,
    SEARCH_POLICY,
    build_search_members,
    validate_search_bundle,
)


@pytest.fixture
def source_archive(tmp_path: Path) -> tuple[Path, list[dict]]:
    source, archive, directory = archived(tmp_path)
    try:
        publish(source, archive, directory)
    finally:
        archive.close()
    entities, _, _ = collect_artifacts(tmp_path, [source.id])
    return tmp_path, entities


def register(root: Path, entity: dict, **changes: object) -> str:
    candidate = MediaCandidate(
        **{
            "url": "https://images.example/original.jpg",
            "references": [
                MediaReference(entity["id"], entity["evidence"][0]["id"], "Side view")
            ],
            "page_url": entity["evidence"][0]["url"],
            **changes,
        }
    )
    with MediaStore(root) as store:
        store.register(entity["source"], "fixture", [candidate])
    return media_id(entity["source"], candidate.url)


def bundle(
    root: Path, entities: list[dict], members: dict[str, bytes], metadata: dict
) -> tuple[dict, dict[str, bytes]]:
    members = {
        **members,
        "index.json": canonical(
            [{key: row[key] for key in ("id", "source")} for row in entities]
        ),
        **{
            "entities/" + row["id"].replace(":", "/") + ".json": canonical(row)
            for row in entities
        },
    }
    manifest = {
        "format_version": 2,
        "entities": len(entities),
        "search": metadata,
        "files": {name: sha256(body) for name, body in members.items()},
    }
    return manifest, members


def check(
    root: Path,
    manifest: dict,
    members: dict[str, bytes],
    entities: list[dict] | None = None,
) -> dict:
    write_bundle(root / "validation.zip", members)
    with zipfile.ZipFile(root / "validation.zip") as archive:
        return validate_search_bundle(archive, manifest, entities)


def test_source_captions_include_unindexed_media_without_mutating_entities(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    entity = entities[0]
    unchanged = copy.deepcopy(entities)
    identity = register(
        root,
        entity,
        caption="<b>Publisher</b> [caption](https://example.test)   antenna",
    )
    members, metadata = build_search_members(root, [entity["source"]], entities)
    assert metadata == {**SEARCH_POLICY, "captions": 2}
    assert json.loads(members[CAPTIONS_MEMBER]) == [
        {
            "entity": 0,
            "evidence_id": entity["evidence"][0]["id"],
            "text": text,
            "media_id": identity,
        }
        for text in ["Publisher caption antenna", "Side view"]
    ]
    assert entities == unchanged
    assert not (root / "media/originals").exists()
    manifest, packed = bundle(root, entities, members, metadata)
    assert check(root, manifest, packed) == metadata
    assert check(root, manifest, packed, entities) == metadata


def test_missing_media_is_a_valid_empty_caption_index(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    members, metadata = build_search_members(root, [entities[0]["source"]], entities)
    assert members == {CAPTIONS_MEMBER: b"[]"}
    assert metadata["captions"] == 0
    assert not (root / "media").exists()
    manifest, packed = bundle(root, entities, members, metadata)
    assert check(root, manifest, packed) == metadata


def test_new_ranking_policy_retains_legacy_bundle_validation(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    members, metadata = build_search_members(root, [entities[0]["source"]], entities)
    assert metadata["version"] == "bm25-minilm-v2"
    legacy = {**metadata, "version": "bm25-minilm-v1"}
    manifest, packed = bundle(root, entities, members, legacy)
    assert check(root, manifest, packed) == legacy


def test_duplicate_resolutions_do_not_repeat_captions_and_exclusions_stay_out(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    entity = entities[0]
    first = register(root, entity)
    second = register(
        root,
        entity,
        url="https://images.example/small.jpg",
        role="preview",
        original_url="https://images.example/original.jpg",
    )
    register(
        root,
        entity,
        url="https://images.example/logo.jpg",
        caption="Excluded brand logo",
        exclusion_reason="logo",
    )
    register(
        root,
        entity,
        url="https://images.example/unassociated.jpg",
        references=[],
        caption="Unassociated photograph",
    )
    members, metadata = build_search_members(root, [entity["source"]], entities)
    assert metadata["captions"] == 1
    assert json.loads(members[CAPTIONS_MEMBER])[0]["media_id"] == min(first, second)
    assert build_search_members(root, [entity["source"]] * 2, entities) == (
        members,
        metadata,
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_same_owner_pair_does_not_leak_excluded_captions(
    source_archive: tuple[Path, list[dict]],
    legacy: bool,
) -> None:
    root, entities = source_archive
    entity = entities[0]
    identity = register(root, entity, caption="Publisher caption")
    register(
        root,
        entity,
        caption="Unrelated navigation caption",
        section="Navigation",
        references=[
            MediaReference(
                entity["id"],
                entity["evidence"][0]["id"],
                "Unrelated navigation caption",
                "Navigation",
            )
        ],
        exclusion_reason="navigation_image",
    )
    if legacy:
        with MediaStore(root) as store:
            record = store.records(entity["source"], "fixture")[0]
            for occurrence in record["occurrences"]:
                occurrence.pop("reference_contexts", None)
            with store.db:
                store._put(record)
    members, _ = build_search_members(
        root,
        [entity["source"]],
        entities,
        allowed_media_ids={identity},
    )
    assert {row["text"] for row in json.loads(members[CAPTIONS_MEMBER])} == {
        "Publisher caption",
        "Side view",
    }


def test_rich_occurrence_matches_exact_ref_even_when_publisher_caption_differs(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    entity = entities[0]
    identity = register(
        root, entity, caption="Publisher caption", section="Description"
    )
    register(
        root,
        entity,
        caption="Publisher menu label",
        section="Navigation",
        references=[
            MediaReference(
                entity["id"],
                entity["evidence"][0]["id"],
                "Unrelated drone caption",
                "A different source section",
                True,
                "source_filename",
            )
        ],
        exclusion_reason="navigation_image",
    )
    members, _ = build_search_members(
        root,
        [entity["source"]],
        entities,
        allowed_media_ids={identity},
    )
    assert {row["text"] for row in json.loads(members[CAPTIONS_MEMBER])} == {
        "Publisher caption",
        "Side view",
    }


def test_reference_cannot_attach_captions_to_another_entity_or_evidence(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    entity = entities[0]
    register(
        root,
        entity,
        references=[MediaReference(entity["id"], "absent-evidence", "Invented name")],
    )
    with pytest.raises(ValueError, match="absent"):
        build_search_members(root, [entity["source"]], entities)


def test_restricted_gallery_keeps_captions_from_selected_media_only(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    entity = entities[0]
    gallery = register(root, entity, caption="Gallery antenna")
    held_out = register(
        root,
        entity,
        url="https://images.example/held-out.jpg",
        caption="Held-out photograph description",
    )
    members, metadata = build_search_members(
        root, [entity["source"]], entities, allowed_media_ids={gallery}
    )
    rows = json.loads(members[CAPTIONS_MEMBER])
    assert metadata["captions"] == 2
    assert [row["text"] for row in rows] == ["Gallery antenna", "Side view"]
    assert {row["media_id"] for row in rows} == {gallery}
    assert held_out not in members[CAPTIONS_MEMBER].decode()
    empty, _ = build_search_members(
        root, [entity["source"]], entities, allowed_media_ids=set()
    )
    assert empty[CAPTIONS_MEMBER] == b"[]"
    with pytest.raises(ValueError, match="unknown media"):
        build_search_members(
            root, [entity["source"]], entities, allowed_media_ids={"unknown"}
        )


def test_occurrence_must_belong_to_its_evidence_page(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    register(root, entities[0], page_url="https://example.test/other", caption="View")
    with pytest.raises(ValueError, match="does not belong"):
        build_search_members(root, [entities[0]["source"]], entities)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("version", "future-version"),
        ("k1", True),
        ("k1", 0),
        ("k1", 1.3),
        ("b", 0.5),
        ("b", 1.01),
        ("lexical_weight", float("nan")),
        ("semantic_weight", float("inf")),
        ("rank_constant", 0),
        ("rank_constant", 60.0),
        ("captions", -1),
        ("captions", 100_001),
        ("undeclared", 1),
    ],
)
def test_invalid_ranking_policy_is_rejected(
    tmp_path: Path, field: str, value: object
) -> None:
    metadata = {**SEARCH_POLICY, "captions": 0, field: value}
    manifest, members = bundle(tmp_path, [], {CAPTIONS_MEMBER: b"[]"}, metadata)
    with pytest.raises(ValueError, match="policy"):
        check(tmp_path, manifest, members)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("entity", True),
        ("entity", 1),
        ("evidence_id", "missing"),
        ("text", ""),
        ("text", " a  b "),
        ("text", "<b>Unnormalized</b>"),
        ("text", "a" * 16_385),
        ("media_id", "other:media:" + "a" * 24),
        ("undeclared", "field"),
    ],
)
def test_caption_schema_scope_and_normalization_are_validated(
    source_archive: tuple[Path, list[dict]], field: str, value: object
) -> None:
    root, entities = source_archive
    register(root, entities[0])
    members, metadata = build_search_members(root, [entities[0]["source"]], entities)
    rows = json.loads(members[CAPTIONS_MEMBER])
    rows[0][field] = value
    members[CAPTIONS_MEMBER] = canonical(rows)
    manifest, packed = bundle(root, entities, members, metadata)
    with pytest.raises(ValueError, match="[Cc]aption"):
        check(root, manifest, packed)


def test_captions_reject_duplicates_and_wrong_order(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    register(root, entities[0], caption="Other caption")
    members, metadata = build_search_members(root, [entities[0]["source"]], entities)
    rows = json.loads(members[CAPTIONS_MEMBER])
    for invalid in [rows[::-1], [rows[0], rows[0]]]:
        members[CAPTIONS_MEMBER] = canonical(invalid)
        manifest, packed = bundle(root, entities, members, metadata)
        with pytest.raises(ValueError, match="sorted and unique"):
            check(root, manifest, packed)


def test_legacy_bundle_has_no_search_descriptor_or_members(tmp_path: Path) -> None:
    manifest = {"format_version": 2, "entities": 0, "files": {}}
    assert check(tmp_path, manifest, {}) == {}
    with pytest.raises(ValueError, match="require a search policy"):
        check(tmp_path, manifest, {CAPTIONS_MEMBER: b"[]"})


def test_caption_members_checksum_count_and_json_are_validated(
    source_archive: tuple[Path, list[dict]],
) -> None:
    root, entities = source_archive
    register(root, entities[0])
    members, metadata = build_search_members(root, [entities[0]["source"]], entities)
    manifest, packed = bundle(root, entities, members, metadata)
    with pytest.raises(ValueError, match="checksum"):
        check(root, manifest, {**packed, CAPTIONS_MEMBER: b"[]"})
    with pytest.raises(ValueError, match="missing, duplicated or undeclared"):
        check(root, manifest, {**packed, "search/extra.json": b"[]"})
    with pytest.raises(ValueError, match="count"):
        check(root, {**manifest, "search": {**metadata, "captions": 2}}, packed)
    with pytest.raises(ValueError, match="checksum"):
        name = "entities/" + entities[0]["id"].replace(":", "/") + ".json"
        check(root, manifest, {**packed, name: b"{}"})
    members[CAPTIONS_MEMBER] = b'[{"entity":0,"entity":1}]'
    manifest, packed = bundle(root, entities, members, metadata)
    with pytest.raises(ValueError, match="Duplicate"):
        check(root, manifest, packed)


class Encoder:
    calls: list[list[str]] = []

    def __init__(self, directory: Path) -> None:
        pass

    def chunks(self, entity: dict) -> list[dict]:
        return [{"text": entity["title"], "evidence_id": ""}]

    def encode(self, texts: list[str]) -> bytes:
        self.calls.append(texts)
        return struct.pack("<ff", 1, 0) * len(texts)


def test_caption_and_policy_changes_only_rebuild_search_identity(
    source_archive: tuple[Path, list[dict]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, entities = source_archive
    monkeypatch.setattr(distribution, "Encoder", Encoder)
    monkeypatch.setattr(distribution, "LOCK", {"dimensions": 2, "files": {}})
    Encoder.calls = []
    output = root / "bundle.zip"
    args = (root, [entities[0]["source"]], root, root / "cache", output)
    first = distribution.package(*args)
    with zipfile.ZipFile(output) as archive:
        before = {name: archive.read(name) for name in archive.namelist()}
    first_calls = len(Encoder.calls)
    register(root, entities[0])
    second = distribution.package(*args)
    assert len(Encoder.calls) == first_calls + 1
    assert len(Encoder.calls[-1]) == 4
    assert first["content_sha256"] == second["content_sha256"]
    assert first["recipe_sha256"] != second["recipe_sha256"]
    assert first["dataset_id"] != second["dataset_id"]
    with zipfile.ZipFile(output) as archive:
        assert all(
            archive.read(name) == body
            for name, body in before.items()
            if name not in {"manifest.json", CAPTIONS_MEMBER}
        )
    assert distribution.package(*args)["changed"] is False
    monkeypatch.setitem(search_distribution.SEARCH_POLICY, "lexical_weight", 0.5)
    third = distribution.package(*args)
    assert third["content_sha256"] == second["content_sha256"]
    assert third["dataset_id"] != second["dataset_id"]
    assert third["search"]["lexical_weight"] == 0.5


def test_search_validation_failure_preserves_existing_output(
    source_archive: tuple[Path, list[dict]], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, entities = source_archive
    monkeypatch.setattr(distribution, "Encoder", Encoder)
    monkeypatch.setattr(distribution, "LOCK", {"dimensions": 2, "files": {}})
    output = root / "bundle.zip"
    args = (root, [entities[0]["source"]], root, root / "cache", output)
    distribution.package(*args)
    original = output.read_bytes()
    register(root, entities[0])

    def reject(*args: object) -> dict:
        raise ValueError("Invalid source caption test")

    monkeypatch.setattr(search_distribution, "validate_search_bundle", reject)
    with pytest.raises(ValueError, match="Invalid source caption test"):
        distribution.package(*args)
    assert output.read_bytes() == original
    assert not output.with_suffix(".pending.zip").exists()
