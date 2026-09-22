import json
import zipfile
from copy import deepcopy
from dataclasses import asdict
from io import BytesIO
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from PIL.MpoImagePlugin import MpoImageFile
from test_pipeline import archived

from pipelines import distribution, image_distribution
from pipelines.build import publish
from pipelines.distribution import canonical, collect_artifacts, sha256, write_bundle
from pipelines.image_preprocess import LEGACY_RECIPE_VERSION, Recipe
from pipelines.media import MediaCandidate, MediaReference, MediaStore, media_id


class ImageEncoder:
    calls: list[str] = []

    @classmethod
    def from_manifest(cls, path: Path) -> "ImageEncoder":
        return cls()

    def encode(self, body: bytes) -> np.ndarray:
        self.calls.append(sha256(body))
        values = (
            np.frombuffer(bytes.fromhex(sha256(body)), np.uint8).astype(np.float32)
            - 128
        )
        return values / np.float32(np.linalg.norm(values.astype(np.float64)))


class TextEncoder:
    def __init__(self, directory: Path) -> None:
        pass

    def chunks(self, entity: dict) -> list[dict]:
        return [{"text": entity["title"], "evidence_id": ""}]

    def encode(self, texts: list[str]) -> bytes:
        return np.tile(np.array([1, 0], dtype="<f4"), (len(texts), 1)).tobytes()


@pytest.fixture
def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, list[dict], Path]:
    from pipelines import image_embedding

    source, archive, directory = archived(tmp_path)
    try:
        publish(source, archive, directory)
    finally:
        archive.close()
    entities, _, _ = collect_artifacts(tmp_path, [source.id])
    model = tmp_path / "image-model"
    model.mkdir()
    graph = b"test model graph"
    lock = {
        "schema_version": 1,
        "id": "fixture/image",
        "variant": "test",
        "checkpoint": {},
        "file": "image.onnx",
        "sha256": sha256(graph),
        "bytes": len(graph),
        "input": "pixels",
        "output": "features",
        "shape": [1, 3, 8, 8],
        "dimensions": 32,
        "normalization": "l2",
        "preprocess": asdict(Recipe(size=8, resize_shortest_edge=8)),
    }
    path = model / "model.json"
    path.write_bytes(canonical(lock))
    (model / "image.onnx").write_bytes(graph)
    monkeypatch.setattr(image_distribution, "IMAGE_LOCK_PATH", path)
    monkeypatch.setattr(image_embedding, "Encoder", ImageEncoder)
    ImageEncoder.calls = []
    return tmp_path, entities, path


def add(
    setup: tuple[Path, list[dict], Path],
    name: str,
    *,
    color: str = "red",
    original: str = "",
    role: str = "original",
    body: bytes | None = None,
    references: list[MediaReference] | None = None,
    format: str = "PNG",
) -> dict:
    root, entities, _ = setup
    entity = entities[0]
    source = entity["source"]
    url = "https://images.example/" + name
    refs = (
        references
        if references is not None
        else [
            MediaReference(
                entity["id"],
                entity["evidence"][0]["id"],
                "Side view",
                "Equipment",
                True,
            )
        ]
    )
    candidate = MediaCandidate(
        url, refs, role=role, original_url=original, caption="Publisher caption"
    )
    path = root / "input-image"
    if body is None:
        image = Image.new("RGB", (24, 16), color)
        if format == "MPO":
            image.save(
                path,
                format=format,
                save_all=True,
                append_images=[Image.new("RGB", (24, 16), "blue")],
            )
        else:
            image.save(path, format=format)
    else:
        path.write_bytes(body)
    with MediaStore(root) as store:
        store.register(source, "fixture", [candidate])
        row = store.save(
            source,
            "fixture",
            media_id(source, url),
            path,
            content_type={
                "PNG": "image/png",
                "JPEG": "image/jpeg",
                "WEBP": "image/webp",
                "GIF": "image/gif",
                "MPO": "image/mpo",
            }[format],
        )
    return row


def build(
    setup: tuple[Path, list[dict], Path], selection: Path | None = None
) -> tuple[dict, dict, dict]:
    root, entities, model = setup
    return image_distribution.build_image_members(
        root, [entities[0]["source"]], entities, model, selection
    )


def validate(
    setup: tuple[Path, list[dict], Path], members: dict, metadata: dict
) -> dict:
    root, entities, _ = setup
    manifest = {
        "format_version": 3,
        "sources": [entities[0]["source"]],
        "image": metadata,
        "files": {name: sha256(body) for name, body in members.items()},
    }
    output = root / "images.zip"
    write_bundle(output, members)
    with zipfile.ZipFile(output) as archive:
        return image_distribution.validate_image_bundle(archive, manifest, entities)


def test_duplicates_siblings_cached_vectors_and_byte_determinism(
    setup: tuple[Path, list[dict], Path],
) -> None:
    original = add(setup, "original.png")
    duplicate = add(setup, "duplicate.png")
    preview = add(
        setup, "small.png", original=original["url"], role="preview", color="blue"
    )
    members, metadata, report = build(setup)
    rows = {row["id"]: row for row in json.loads(members["image/index.json"])}
    assert metadata["vectors"] == 1
    assert (
        rows[original["id"]]["vector_index"]
        == rows[duplicate["id"]]["vector_index"]
        == 0
    )
    assert rows[preview["id"]]["exclusion_reason"] == "alternate_resolution"
    assert rows[original["id"]]["references"][0]["ambiguous"] is True
    assert rows[original["id"]]["caption"] == "Publisher caption"
    assert report["outcomes"] == {"indexed": 2, "alternate_resolution": 1}
    assert ImageEncoder.calls.count(original["sha256"]) == 1
    assert validate(setup, members, metadata)["vectors"] == 1
    assert build(setup) == (members, metadata, report)
    assert ImageEncoder.calls.count(original["sha256"]) == 1
    assert original["sha256"] not in {sha256(body) for body in members.values()}


def test_new_recipe_invalidates_vector_cache_and_validates_old_bundle(
    setup: tuple[Path, list[dict], Path],
) -> None:
    _, _, model = setup
    current = model.read_bytes()
    legacy = json.loads(current)
    legacy["preprocess"]["version"] = LEGACY_RECIPE_VERSION
    model.write_bytes(canonical(legacy))
    original = add(setup, "original.jpg", format="JPEG")
    old_members, old_metadata, old_report = build(setup)
    assert ImageEncoder.calls.count(original["sha256"]) == 1
    model.write_bytes(current)
    assert validate(setup, old_members, old_metadata)["vectors"] == 1
    new_members, new_metadata, new_report = build(setup)
    assert ImageEncoder.calls.count(original["sha256"]) == 2
    assert old_report["vector_recipe"] != new_report["vector_recipe"]
    assert old_members["image/model.json"] != new_members["image/model.json"]
    assert old_members["image/image.onnx"] == new_members["image/image.onnx"]
    assert build(setup) == (new_members, new_metadata, new_report)
    assert ImageEncoder.calls.count(original["sha256"]) == 2


@pytest.mark.parametrize("change", ["version", "mean", "input", "weights"])
def test_legacy_bundle_compatibility_rejects_other_model_changes(
    setup: tuple[Path, list[dict], Path], change: str
) -> None:
    add(setup, "original.jpg", format="JPEG")
    members, metadata, _ = build(setup)
    legacy = json.loads(members["image/model.json"])
    legacy["preprocess"]["version"] = LEGACY_RECIPE_VERSION
    if change == "version":
        legacy["preprocess"]["version"] = "unknown"
    elif change == "mean":
        legacy["preprocess"]["mean"][0] = 0.1
    elif change == "input":
        legacy["input"] = "different"
    else:
        members["image/image.onnx"] += b"changed"
    members["image/model.json"] = canonical(legacy)
    with pytest.raises(ValueError, match="pinned lock"):
        validate(setup, members, metadata)


def test_new_build_rejects_legacy_model_recipe(
    setup: tuple[Path, list[dict], Path],
) -> None:
    root, _, model = setup
    legacy = json.loads(model.read_bytes())
    legacy["preprocess"]["version"] = LEGACY_RECIPE_VERSION
    supplied = root / "old-model.json"
    supplied.write_bytes(canonical(legacy))
    with pytest.raises(ValueError, match="pinned model contract"):
        image_distribution._model(supplied)


def test_selection_does_not_read_unselected_originals(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    wanted, excluded = (
        add(setup, "chosen.png"),
        add(setup, "excluded.png", color="blue"),
    )
    selection = setup[0] / "selection.json"
    selection.write_bytes(canonical({"schema_version": 1, "media_ids": [wanted["id"]]}))
    body = MediaStore.body

    def guarded(store: MediaStore, digest: str) -> bytes:
        assert digest != excluded["sha256"], "Read an unselected original"
        return body(store, digest)

    monkeypatch.setattr(MediaStore, "body", guarded)
    members, metadata, report = build(setup, selection)
    assert metadata["vectors"] == 1
    assert report["outcomes"] == {"indexed": 1, "selection": 1}
    validate(setup, members, metadata)


def test_compact_preview_preserves_full_view_and_original_vectors(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    original = Image.new("RGB", (1200, 600), "blue")
    original.paste("red", (0, 0, 100, 600))
    original.paste("green", (1100, 0, 1200, 600))
    body = BytesIO()
    original.save(body, format="PNG")
    captured = add(setup, "wide.png", body=body.getvalue())
    members, _, report = build(setup)
    row = json.loads(members["image/index.json"])[0]
    preview = Image.open(BytesIO(members[row["preview"]["member"]]))
    assert preview.size == (320, 160)
    left, right = preview.getpixel((2, 80)), preview.getpixel((317, 80))
    assert isinstance(left, tuple) and isinstance(right, tuple)
    assert left[0] > 200
    assert right[1] > 100
    assert ImageEncoder.calls.count(captured["sha256"]) == 1
    original_vectors = members["image/vectors.f16"]
    original_recipe = report["preview_recipe"]
    monkeypatch.setattr(image_distribution, "PREVIEW_EDGE", 256)
    changed, _, changed_report = build(setup)
    changed_row = json.loads(changed["image/index.json"])[0]
    assert changed_row["preview"]["width"] == 256
    assert changed_row["preview"]["sha256"] != row["preview"]["sha256"]
    assert changed_report["preview_recipe"] != original_recipe
    assert changed["image/vectors.f16"] == original_vectors
    assert ImageEncoder.calls.count(captured["sha256"]) == 1


def test_sibling_reference_union_preserves_publisher_context(
    setup: tuple[Path, list[dict], Path],
) -> None:
    original = add(setup, "original.png")
    entity = setup[1][0]
    add(
        setup,
        "preview.png",
        color="blue",
        original=original["url"],
        role="preview",
        references=[
            MediaReference(
                entity["id"], entity["evidence"][0]["id"], "Front view", "Gallery"
            )
        ],
    )
    members, metadata, _ = build(setup)
    row = next(
        row
        for row in json.loads(members["image/index.json"])
        if row["id"] == original["id"]
    )
    assert {ref["caption"] for ref in row["references"]} == {"Front view", "Side view"}
    validate(setup, members, metadata)


def test_saved_excluded_context_never_becomes_active_reference(
    setup: tuple[Path, list[dict], Path],
) -> None:
    row = add(setup, "context.png")
    entity = setup[1][0]
    reference = MediaReference(
        entity["id"], entity["evidence"][0]["id"], "Side view", "Equipment", True
    )
    with MediaStore(setup[0]) as store:
        store.register(
            entity["source"],
            "fixture",
            [
                MediaCandidate(
                    row["url"],
                    [reference],
                    "unrelated_illustration",
                    caption="Publisher caption",
                )
            ],
        )
    members, metadata, report = build(setup)
    assert metadata["vectors"] == 0
    assert json.loads(members["image/index.json"])[0]["references"] == []
    assert (
        report["association_exclusions"][0]["references"][0]["entity_id"]
        == entity["id"]
    )
    assert row["sha256"] not in ImageEncoder.calls
    validate(setup, members, metadata)


def test_excluded_reference_context_does_not_leak_through_an_eligible_owner_pair(
    setup: tuple[Path, list[dict], Path],
) -> None:
    row = add(setup, "mixed-context.png")
    entity = setup[1][0]
    excluded = MediaReference(
        entity["id"],
        entity["evidence"][0]["id"],
        "Unrelated drone caption",
        "Related article",
        True,
        "source_filename",
    )
    with MediaStore(setup[0]) as store:
        store.register(
            entity["source"],
            "fixture",
            [
                MediaCandidate(
                    row["url"],
                    [excluded],
                    "navigation_image",
                    caption="Publisher navigation label",
                    section="Navigation",
                )
            ],
        )
    members, metadata, report = build(setup)
    indexed = json.loads(members["image/index.json"])[0]
    assert metadata["vectors"] == 1
    assert indexed["references"] == [
        asdict(
            MediaReference(
                entity["id"],
                entity["evidence"][0]["id"],
                "Side view",
                "Equipment",
                True,
            )
        )
    ]
    assert indexed["caption"] == "Publisher caption"
    assert report["association_exclusions"][0]["references"] == [asdict(excluded)]
    validate(setup, members, metadata)


def test_rich_reference_identity_keeps_ambiguity_and_legacy_context() -> None:
    allowed = asdict(MediaReference("example:1", "page", "Antenna", "Lead"))
    removed = {**allowed, "ambiguous": True, "association": "source_filename"}
    legacy = {
        **allowed,
        "caption": "Supplementary source caption",
        "section": "Gallery",
    }
    pair = {key: allowed[key] for key in ("entity_id", "evidence_id")}
    record: dict = {
        "source": "example",
        "references": [allowed, removed, legacy],
        "occurrences": [
            {
                "associated": True,
                "exclusion_reason": "",
                "references": [pair],
                "reference_contexts": [allowed],
                "caption": "Publisher label",
                "section": "Description",
            },
            {
                "associated": True,
                "exclusion_reason": "navigation_image",
                "references": [pair],
                "reference_contexts": [removed],
                "caption": "Publisher label",
                "section": "Description",
            },
        ],
    }
    active, excluded = image_distribution._references(record, {"example:1": {"page"}})
    assert active == [allowed]
    assert {canonical(ref) for ref in excluded} == {
        canonical(removed),
        canonical(legacy),
    }
    record["occurrences"].append(
        {
            "associated": True,
            "exclusion_reason": "",
            "references": [pair],
            "caption": "Other publisher caption",
            "section": "Gallery",
        }
    )
    active, excluded = image_distribution._references(record, {"example:1": {"page"}})
    assert {canonical(ref) for ref in active} == {canonical(allowed), canonical(legacy)}
    assert excluded == [removed]
    record.pop("occurrences")
    active, excluded = image_distribution._references(record, {"example:1": {"page"}})
    assert len(active) == 3 and excluded == []


def test_capture_failure_and_unsupported_format_remain_explicit(
    setup: tuple[Path, list[dict], Path],
) -> None:
    add(setup, "unsupported.webp", format="WEBP")
    entity = setup[1][0]
    url = "https://images.example/failed.png"
    with MediaStore(setup[0]) as store:
        store.register(
            entity["source"],
            "fixture",
            [
                MediaCandidate(
                    url, [MediaReference(entity["id"], entity["evidence"][0]["id"])]
                )
            ],
        )
        store.mark(
            entity["source"],
            "fixture",
            media_id(entity["source"], url),
            "failed",
            "http_404",
        )
    members, metadata, report = build(setup)
    assert metadata["records"] == 1 and metadata["vectors"] == 0
    assert report["outcomes"] == {"unsupported_image_format": 1}
    assert report["capture_outcomes"][0]["error"] == "http_404"
    validate(setup, members, metadata)


@pytest.mark.parametrize("selected", [True, False])
@pytest.mark.parametrize("format", ["GIF", "MPO"])
def test_archival_formats_remain_metadata_only_in_image_bundle(
    setup: tuple[Path, list[dict], Path], selected: bool, format: str
) -> None:
    from pipelines.image_preprocess import preprocess

    original = add(setup, "original." + format.lower(), format=format)
    selection = setup[0] / "selection.json"
    selection.write_bytes(
        canonical(
            {
                "schema_version": 1,
                "media_ids": [original["id"]] if selected else [],
            }
        )
    )
    members, metadata, report = build(setup, selection)
    (row,) = json.loads(members["image/index.json"])
    assert row["content_type"] == "image/" + format.lower()
    if format == "MPO":
        assert row["frame_count"] == row["validated_frame_count"] == 2
        assert row["unreadable_frames"] == []
    assert row["sha256"] == original["sha256"]
    assert row["exclusion_reason"] == "unsupported_image_format"
    assert row["vector_index"] is row["preview"] is None
    assert metadata["vectors"] == metadata["preview_bytes"] == 0
    assert members["image/vectors.f16"] == b""
    assert not any(name.startswith("image/previews/") for name in members)
    assert original["sha256"] not in ImageEncoder.calls
    assert report["outcomes"] == {"unsupported_image_format": 1}
    validate(setup, members, metadata)
    with MediaStore(setup[0], read_only=True) as store:
        body = store.body(original["sha256"])
    with pytest.raises(ValueError, match="JPEG"):
        preprocess(body, Recipe())


@pytest.mark.parametrize("change", ["reason", "vector", "preview"])
@pytest.mark.parametrize("format", ["GIF", "MPO"])
def test_archival_formats_cannot_enable_image_encoding(
    setup: tuple[Path, list[dict], Path], change: str, format: str
) -> None:
    add(setup, "original." + format.lower(), format=format)
    members, metadata, _ = build(setup)
    rows = json.loads(members["image/index.json"])
    if change == "reason":
        rows[0]["exclusion_reason"] = "selection"
    elif change == "vector":
        rows[0]["vector_index"] = 0
    else:
        rows[0]["preview"] = {}
    members["image/index.json"] = canonical(rows)
    metadata["gallery_sha256"] = sha256(members["image/index.json"])
    with pytest.raises(ValueError, match="GIF/MPO metadata"):
        validate(setup, members, metadata)


@pytest.mark.parametrize(
    "frames,validated,unreadable",
    [
        (0, 0, []),
        (65, 65, []),
        (2, 2, [1]),
        (2, 1, [0]),
        (2, 1, [2]),
        (3, 1, [1, 1]),
        (2, 1, [True]),
        (True, 1, []),
    ],
)
def test_mpo_bundle_rejects_invalid_frame_metadata(
    setup: tuple[Path, list[dict], Path], frames: int, validated: int, unreadable: list
) -> None:
    add(setup, "original.mpo", format="MPO")
    members, metadata, _ = build(setup)
    rows = json.loads(members["image/index.json"])
    rows[0].update(
        frame_count=frames,
        validated_frame_count=validated,
        unreadable_frames=unreadable,
    )
    members["image/index.json"] = canonical(rows)
    metadata["gallery_sha256"] = sha256(members["image/index.json"])
    with pytest.raises(ValueError, match="MPO frame metadata"):
        validate(setup, members, metadata)


def test_missing_mpo_secondary_is_retained_in_metadata_only_bundle(
    setup: tuple[Path, list[dict], Path],
) -> None:
    path = setup[0] / "missing.mpo"
    first = Image.new("RGB", (24, 16), "red")
    first.save(
        path,
        format="MPO",
        save_all=True,
        append_images=[Image.new("RGB", (24, 16), "blue")],
    )
    with Image.open(path) as image:
        assert isinstance(image, MpoImageFile)
        image.seek(1)
        body = path.read_bytes()[: image.offset]
    original = add(setup, "missing.mpo", format="MPO", body=body)
    members, metadata, _ = build(setup)
    (row,) = json.loads(members["image/index.json"])
    assert row["frame_count"] == 2
    assert row["validated_frame_count"] == 1
    assert row["unreadable_frames"] == [1]
    assert row["sha256"] == original["sha256"]
    assert row["exclusion_reason"] == "unsupported_image_format"
    validate(setup, members, metadata)


def test_diverse_views_and_preview_budget_are_explicit(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(12):
        add(setup, f"view{index}.png", color=f"#{index * 19:02x}8060")
    members, metadata, report = build(setup)
    assert metadata["vectors"] == 8
    assert report["outcomes"] == {"indexed": 8, "view_budget": 4}
    validate(setup, members, metadata)
    monkeypatch.setattr(image_distribution, "MAX_PREVIEW_BYTES", 1)
    members, metadata, report = build(setup)
    assert metadata["vectors"] == metadata["preview_bytes"] == 0
    assert report["outcomes"] == {"preview_budget": 8, "view_budget": 4}
    validate(setup, members, metadata)


def test_preview_budget_covers_entities_before_extra_views(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    from pipelines import snapshot

    root, entities, _ = setup
    manifest, _ = snapshot.load_snapshot(root / entities[0]["source"])
    second = deepcopy(entities[0])
    second["id"] = second["source"] + ":second"
    entities.append(second)
    monkeypatch.setattr(
        snapshot, "load_snapshot", lambda directory: (manifest, entities)
    )
    pictures = []
    for color in ("red", "green", "blue", "yellow"):
        stream = BytesIO()
        Image.new("RGB", (24, 16), color).save(stream, format="PNG")
        pictures.append(stream.getvalue())
    pictures.sort(key=sha256)
    for position, body in enumerate(pictures):
        owner = entities[0] if position < 3 else second
        add(
            setup,
            f"view-{position}.png",
            body=body,
            references=[MediaReference(owner["id"], owner["evidence"][0]["id"])],
        )
    members, _, _ = build(setup)
    sizes = [
        len(body)
        for name, body in members.items()
        if name.startswith("image/previews/")
    ]
    assert 2 * max(sizes) < 3 * min(sizes)
    monkeypatch.setattr(image_distribution, "MAX_PREVIEW_BYTES", 2 * max(sizes))
    members, metadata, report = build(setup)
    indexed = [
        row
        for row in json.loads(members["image/index.json"])
        if row["vector_index"] is not None
    ]
    assert {ref["entity_id"] for row in indexed for ref in row["references"]} == {
        entity["id"] for entity in entities
    }
    assert metadata["vectors"] == 2
    assert report["outcomes"] == {"indexed": 2, "preview_budget": 2}
    validate(setup, members, metadata)


@pytest.mark.parametrize(
    "failure",
    ["nan", "norm", "size", "reference", "ownership", "preview", "model", "orphan"],
)
def test_rejects_corrupt_declared_image_artifacts(
    setup: tuple[Path, list[dict], Path], failure: str
) -> None:
    add(setup, "view.png")
    members, metadata, _ = build(setup)
    if failure in {"nan", "norm", "size"}:
        values = np.frombuffer(members["image/vectors.f16"], "<f2").copy()
        values[0] = np.nan if failure == "nan" else 5
        members["image/vectors.f16"] = values.tobytes() if failure != "size" else b"a"
    elif failure in {"reference", "ownership"}:
        rows = json.loads(members["image/index.json"])
        if failure == "reference":
            rows[0]["references"][0]["evidence_id"] = "missing"
        else:
            rows[0]["vector_index"] = 1
        members["image/index.json"] = canonical(rows)
        metadata["gallery_sha256"] = sha256(members["image/index.json"])
    elif failure == "preview":
        name = next(name for name in members if name.startswith("image/previews/"))
        members[name] = b"not an image"
    elif failure == "model":
        members["image/image.onnx"] = b"changed graph"
    else:
        members["image/unexpected"] = b"orphan"
    with pytest.raises(ValueError):
        validate(setup, members, metadata)


def test_model_contract_and_unknown_selection_rejected(
    setup: tuple[Path, list[dict], Path],
) -> None:
    root, _, model = setup
    other = root / "other.json"
    supplied = json.loads(model.read_bytes())
    supplied["preprocess"]["mean"] = [0.5, 0.5, 0.5]
    other.write_bytes(canonical(supplied))
    with pytest.raises(ValueError, match="pinned"):
        image_distribution._model(other)
    other.write_bytes(canonical({"schema_version": 1, "media_ids": ["unknown"]}))
    with pytest.raises(ValueError, match="unknown"):
        build(setup, other)


def test_package_default_stays_format2_and_image_changes_only_recipe(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, entities, image_model = setup
    monkeypatch.setattr(distribution, "Encoder", TextEncoder)
    monkeypatch.setattr(
        distribution,
        "LOCK",
        {"dimensions": 2, "files": {"text.onnx": {}}, "id": "text-test"},
    )
    (root / "text.onnx").write_bytes(b"text graph")
    arguments = (root, [entities[0]["source"]], root, root / "cache")
    add(setup, "view.png")
    first = distribution.package(*arguments, root / "text.zip")
    assert first["format_version"] == 2 and "image" not in first
    expected = sha256(
        canonical(
            {
                "format": 2,
                "storage": distribution.STORAGE_VERSION,
                "search": {
                    "metadata": first["search"],
                    "files": {
                        "search/captions.json": first["files"]["search/captions.json"]
                    },
                },
                "model": distribution.LOCK,
                "research": {
                    "metadata": first["research"],
                    "files": {
                        name: digest
                        for name, digest in first["files"].items()
                        if name.startswith("research/")
                    },
                },
            }
        )
    )
    assert first["recipe_sha256"] == expected
    second = distribution.package(
        *arguments, root / "image.zip", image_model=image_model
    )
    assert second["format_version"] == 3
    assert first["content_sha256"] == second["content_sha256"]
    assert first["dataset_id"] != second["dataset_id"]
    with (
        zipfile.ZipFile(root / "text.zip") as text,
        zipfile.ZipFile(root / "image.zip") as image,
    ):
        for name in text.namelist():
            if name != "manifest.json":
                assert text.read(name) == image.read(name)
    assert (
        distribution.package(*arguments, root / "image.zip", image_model=image_model)[
            "changed"
        ]
        is False
    )
    add(setup, "more.png", color="blue")
    third = distribution.package(
        *arguments, root / "image.zip", image_model=image_model
    )
    assert third["changed"] is True
    assert second["content_sha256"] == third["content_sha256"]
    assert second["dataset_id"] != third["dataset_id"]
    refreshed = distribution.package(*arguments, root / "text.zip")
    assert refreshed["content_sha256"] == first["content_sha256"]
    assert refreshed["files"]["vectors.f32"] == first["files"]["vectors.f32"]
    assert distribution.package(*arguments, root / "text.zip")["changed"] is False
    monkeypatch.setattr(distribution, "STORAGE_VERSION", "alternative-storage-policy")
    repackaged = distribution.package(*arguments, root / "text.zip")
    assert repackaged["changed"] is True
    assert repackaged["content_sha256"] == refreshed["content_sha256"]
    assert repackaged["dataset_id"] != refreshed["dataset_id"]
    assert repackaged["files"] == refreshed["files"]
    assert distribution.package(*arguments, root / "text.zip")["changed"] is False
    with pytest.raises(ValueError, match="requires"):
        distribution.package(
            *arguments, root / "invalid.zip", image_selection=root / "selection.json"
        )


def test_image_validation_failure_preserves_previous_output(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, entities, image_model = setup
    monkeypatch.setattr(distribution, "Encoder", TextEncoder)
    monkeypatch.setattr(
        distribution,
        "LOCK",
        {"dimensions": 2, "files": {"text.onnx": {}}, "id": "text-test"},
    )
    (root / "text.onnx").write_bytes(b"text graph")
    arguments = (
        root,
        [entities[0]["source"]],
        root,
        root / "cache",
        root / "bundle.zip",
    )
    distribution.package(*arguments)
    previous = (root / "bundle.zip").read_bytes()
    add(setup, "view.png")

    def reject(*args: object) -> dict:
        raise ValueError("Invalid generated image bundle")

    monkeypatch.setattr(image_distribution, "validate_image_bundle", reject)
    with pytest.raises(ValueError, match="Invalid generated"):
        distribution.package(*arguments, image_model=image_model)
    assert (root / "bundle.zip").read_bytes() == previous
    assert not (root / "bundle.pending.zip").exists()


def test_package_selection_restricts_search_captions_to_indexed_media(
    setup: tuple[Path, list[dict], Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    root, entities, image_model = setup
    entity = entities[0]
    wanted = add(setup, "gallery.png")
    add(
        setup,
        "held-out.png",
        color="blue",
        references=[
            MediaReference(
                entity["id"], entity["evidence"][0]["id"], "Held-out antenna caption"
            )
        ],
    )
    selection = root / "selection.json"
    selection.write_bytes(canonical({"schema_version": 1, "media_ids": [wanted["id"]]}))
    monkeypatch.setattr(distribution, "Encoder", TextEncoder)
    monkeypatch.setattr(distribution, "LOCK", {"dimensions": 2, "files": {}})
    output = root / "selected.zip"
    distribution.package(
        root,
        [entity["source"]],
        root,
        root / "cache",
        output,
        image_model=image_model,
        image_selection=selection,
    )
    with zipfile.ZipFile(output) as archive:
        captions = json.loads(archive.read("search/captions.json"))
        assert {row["media_id"] for row in captions} == {wanted["id"]}
        assert {row["text"] for row in captions} == {"Publisher caption", "Side view"}


def test_canonical_model_lock_ignores_input_formatting_and_export_telemetry(
    setup: tuple[Path, list[dict], Path],
) -> None:
    _, _, model = setup
    before = build(setup)
    lock = json.loads(model.read_bytes())
    model.write_text(json.dumps(lock, indent=4), encoding="utf-8")
    assert build(setup) == before
    supplied = model.with_name("export.json")
    supplied.write_bytes(canonical({**lock, "export_probe": {"seconds": 3.0}}))
    assert image_distribution._model(supplied)[1] == canonical(lock)
