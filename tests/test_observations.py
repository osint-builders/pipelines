import json
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

import pytest
from test_image_distribution import add, build
from test_image_distribution import setup as setup

from pipelines import observations
from pipelines.distribution import write_bundle
from pipelines.media import MediaStore, ProcessingRecipe


@dataclass
class Gallery:
    root: Path
    entities: list[dict]
    model: Path
    bundle: Path
    members: dict[str, bytes]
    manifest: dict
    images: list[dict]


@pytest.fixture
def gallery(setup: tuple[Path, list[dict], Path]) -> Gallery:
    root, entities, model = setup
    add(setup, "original.png")
    add(setup, "identical.png")
    add(setup, "different.png", color="blue")
    members, metadata, _ = build(setup)
    members["index.json"] = observations.canonical(
        [{"id": entity["id"]} for entity in entities]
    )
    for entity in entities:
        members["entities/" + entity["id"].replace(":", "/") + ".json"] = (
            observations.canonical(entity)
        )
    manifest = {
        "format_version": 3,
        "sources": [entities[0]["source"]],
        "image": metadata,
        "files": {name: observations.digest(body) for name, body in members.items()},
    }
    bundle = root / "gallery.zip"
    write_bundle(bundle, {**members, "manifest.json": observations.canonical(manifest)})
    return Gallery(
        root,
        entities,
        model,
        bundle,
        members,
        manifest,
        json.loads(members["image/index.json"]),
    )


def ocr_output() -> dict:
    text = "РАДАР <λ>& 1Л122Е"
    return {
        "text": text,
        "confidence": None,
        "regions": [
            {
                "text": text,
                "confidence": 1.0,
                "polygon": [[0.1, 0.2], [0.8, 0.2], [0.8, 0.4], [0.1, 0.4]],
            }
        ],
    }


class FakeAnalyzer:
    def __init__(
        self, output: dict | None = None, *, revision: str = "1", fail: bool = False
    ) -> None:
        self.recipe = ProcessingRecipe(
            "test-observation-v1", revision, {"threshold": 0.85}, "fixture/ocr"
        )
        self.output = ocr_output() if output is None else output
        self.fail = fail
        self.calls = 0

    def analyze(self, body: bytes) -> dict:
        self.calls += 1
        assert body.startswith(b"\x89PNG")
        if self.fail:
            raise RuntimeError("Failed test inference")
        return deepcopy(self.output)


def analyze(
    gallery: Gallery, analyzer: FakeAnalyzer | None = None
) -> tuple[dict, dict]:
    target = gallery.root / "analysis.json"
    report = observations.observe(
        gallery.root,
        gallery.bundle,
        target,
        analyzers={"ocr": analyzer or FakeAnalyzer()},
    )
    return report, json.loads(target.read_bytes())


def test_cache_reuses_byte_duplicates_and_invalidates_by_recipe(
    gallery: Gallery,
) -> None:
    analyzer = FakeAnalyzer()
    report, first = analyze(gallery, analyzer)
    assert analyzer.calls == 2
    assert report["states"] == {"observed": 3}
    assert len(first["observations"]) == 2
    assert sorted(len(row["media_ids"]) for row in first["observations"]) == [1, 2]
    report, second = analyze(gallery, analyzer)
    assert analyzer.calls == 2 and first == second
    changed = FakeAnalyzer(revision="2")
    _, third = analyze(gallery, changed)
    assert changed.calls == 2
    assert {row["id"] for row in first["observations"]}.isdisjoint(
        row["id"] for row in third["observations"]
    )
    assert first["gallery_sha256"] == third["gallery_sha256"]


def test_failed_inference_is_not_cached_but_empty_results_are(gallery: Gallery) -> None:
    analyzer = FakeAnalyzer(fail=True)
    report, failed = analyze(gallery, analyzer)
    assert report["states"] == {"failed": 3} and not failed["observations"]
    with MediaStore(gallery.root, read_only=True) as store:
        assert all(
            json.loads(row[0])["recipe"]["version"] != analyzer.recipe.version
            for row in store.db.execute("SELECT data FROM derived")
        )
    analyzer.fail = False
    report, _ = analyze(gallery, analyzer)
    assert analyzer.calls == 4 and report["states"] == {"observed": 3}
    empty = FakeAnalyzer(
        {"text": "", "confidence": None, "regions": []}, revision="empty"
    )
    report, value = analyze(gallery, empty)
    assert report["states"] == {"empty": 3} and not value["observations"]
    analyze(gallery, empty)
    assert empty.calls == 2


def test_static_webp_observations_preserve_original_identity_and_png_cache(
    gallery: Gallery,
) -> None:
    analyzer = FakeAnalyzer()
    analyze(gallery, analyzer)
    assert analyzer.calls == 2
    setup = (gallery.root, gallery.entities, gallery.model)
    webp = add(setup, "new.webp", format="WEBP", color="green")
    members, metadata, _ = build(setup)
    members.update(
        (name, body)
        for name, body in gallery.members.items()
        if not name.startswith("image/")
    )
    manifest = {
        **gallery.manifest,
        "image": metadata,
        "files": {name: observations.digest(body) for name, body in members.items()},
    }
    write_bundle(
        gallery.bundle, {**members, "manifest.json": observations.canonical(manifest)}
    )
    report, analysis = analyze(gallery, analyzer)
    assert report["states"] == {"observed": 4}
    assert analyzer.calls == 3
    row = next(
        r for r in analysis["observations"] if r["media_sha256"] == webp["sha256"]
    )
    recipe = analysis["recipes"][row["recipe_sha256"]]
    assert recipe["settings"]["gallery_decode"]["version"] == "static-webp-to-png-v1"
    with MediaStore(gallery.root, read_only=True) as store:
        assert store.body(webp["sha256"]).startswith(b"RIFF")
    _, again = analyze(gallery, analyzer)
    assert again == analysis and analyzer.calls == 3


def test_selection_reads_only_chosen_original_and_keeps_exact_associations(
    gallery: Gallery, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen = gallery.images[0]
    path = gallery.root / "selection.json"
    path.write_bytes(
        observations.canonical({"schema_version": 1, "media_ids": [chosen["id"]]})
    )
    original = MediaStore.body

    def body(store: MediaStore, sha: str) -> bytes:
        assert sha == chosen["sha256"]
        return original(store, sha)

    monkeypatch.setattr(MediaStore, "body", body)
    target = gallery.root / "selected.json"
    report = observations.observe(
        gallery.root,
        gallery.bundle,
        target,
        selection=path,
        analyzers={"ocr": FakeAnalyzer()},
    )
    value = json.loads(target.read_bytes())
    assert report["states"] == {"observed": 1, "selection": 2}
    assert value["observations"][0]["media_ids"] == [chosen["id"]]
    assert value["observations"][0]["references"] == observations.image_references(
        [chosen]
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "gallery",
        "foreign_media",
        "unindexed",
        "sha",
        "evidence",
        "owner",
        "missing_ref",
        "recipe",
        "confidence",
        "polygon",
        "mismatched_text",
        "empty_outcome",
        "missing_outcome",
    ],
)
def test_analysis_rejects_inconsistent_provenance_and_regions(
    gallery: Gallery, mutation: str
) -> None:
    _, value = analyze(gallery)
    images = deepcopy(gallery.images)
    row = value["observations"][0]
    if mutation == "gallery":
        value["gallery_sha256"] = "a" * 64
    elif mutation == "foreign_media":
        row["media_ids"] = ["foreign:media:123"]
    elif mutation == "unindexed":
        next(image for image in images if image["id"] == row["media_ids"][0])[
            "vector_index"
        ] = None
    elif mutation == "sha":
        row["media_sha256"] = "f" * 64
    elif mutation == "evidence":
        row["references"][0]["evidence_id"] = "other"
    elif mutation == "owner":
        row["references"][0]["entity_id"] = "other:entity"
    elif mutation == "missing_ref":
        row["references"] = []
    elif mutation == "recipe":
        value["recipes"][row["recipe_sha256"]]["settings"]["threshold"] = 0.99
    elif mutation == "confidence":
        row["regions"][0]["confidence"] = True
    elif mutation == "polygon":
        row["regions"][0]["polygon"][0] = [10, 20]
    elif mutation == "mismatched_text":
        row["text"] = "Text with no corresponding image region"
    elif mutation == "empty_outcome":
        for outcome in value["outcomes"]:
            if outcome.get("observation_id") == row["id"]:
                outcome["state"] = "empty"
    else:
        value["observations"] = [
            item for item in value["observations"] if item["id"] != row["id"]
        ]
        value["outcomes"] = [
            item
            for item in value["outcomes"]
            if item.get("observation_id") != row["id"]
        ]
    if mutation in {"sha", "confidence", "polygon", "mismatched_text"}:
        old_id = row["id"]
        row["id"] = observations.observation_id(row)
        value["observations"].sort(key=lambda item: item["id"])
        for outcome in value["outcomes"]:
            if outcome.get("observation_id") == old_id:
                outcome["observation_id"] = row["id"]
    with pytest.raises(ValueError):
        observations.validate_analysis(value, gallery.manifest, images)


def test_confidence_one_and_unicode_identity_survive_json_roundtrip(
    gallery: Gallery,
) -> None:
    _, value = analyze(gallery)
    first = value["observations"][0]
    assert first["regions"][0]["confidence"] == 1.0
    for ascii in [False, True]:
        restored = json.loads(json.dumps(value, ensure_ascii=ascii, indent=2))
        observations.validate_analysis(restored, gallery.manifest, gallery.images)
        assert observations.observation_id(restored["observations"][0]) == first["id"]
    core = {key: first[key] for key in observations.CORE_FIELDS}
    encoded = observations.canonical(core)
    assert (
        b'"confidence":1.0' in encoded
        and "РАДАР".encode() in encoded
        and b"<\xce\xbb>&" in encoded
    )


@pytest.mark.parametrize(
    "outcome",
    [
        {"state": "failed"},
        {"state": "failed", "reason": "private file path"},
        {"state": "empty", "observation_id": "stale"},
        {"state": "selection", "reason": "RuntimeError"},
    ],
)
def test_non_observation_outcomes_have_exact_state_fields(
    gallery: Gallery, outcome: dict
) -> None:
    _, value = analyze(gallery, FakeAnalyzer(fail=True))
    value["outcomes"][0] = {
        "media_id": value["outcomes"][0]["media_id"],
        "kind": "ocr",
        **outcome,
    }
    with pytest.raises(ValueError):
        observations.validate_analysis(value, gallery.manifest, gallery.images)


@pytest.mark.parametrize(
    "kind,output",
    [
        ("description", {"text": "A radar", "confidence": 0.8, "regions": []}),
        ("ocr", {"text": "Text", "confidence": None, "regions": []}),
        ("ocr", {"text": "", "confidence": None, "regions": ocr_output()["regions"]}),
        ("ocr", {"text": "x", "confidence": float("nan"), "regions": []}),
    ],
)
def test_output_contract_rejects_invented_confidence_or_untraceable_text(
    kind: str, output: dict
) -> None:
    with pytest.raises(ValueError):
        observations.validate_output(output, kind, allow_empty=True)
