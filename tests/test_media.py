import hashlib
import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from PIL import Image
from PIL.MpoImagePlugin import MpoImageFile

from pipelines.media import (
    MAX_IMAGE_BYTES,
    MediaCandidate,
    MediaReference,
    MediaStore,
    ProcessingRecipe,
    media_id,
    recipe_key,
)

URL = "https://images.example/radar.png"


@pytest.mark.parametrize("reverse", [False, True])
def test_occurrences_keep_context_and_eligible_capture_wins(
    tmp_path: Path, reverse: bool
) -> None:
    items = [
        MediaCandidate(
            URL, exclusion_reason="navigation", page_url="https://example.org/index"
        ),
        MediaCandidate(
            URL,
            [MediaReference("fixture:radar", "evidence-1", "View", "Overview", True)],
            role="preview",
            original_url="https://images.example/original.png",
            page_url="https://example.org/detail",
        ),
    ]
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run", reversed(items) if reverse else items)
        store.register("fixture", "run", items)
        row = store.records("fixture", "run")[0]
    assert row["state"] == "pending"
    assert len(row["occurrences"]) == 2
    assert row["references"][0]["section"] == "Overview"
    assert row["references"][0]["ambiguous"] is True
    detail = next(item for item in row["occurrences"] if item["role"] == "preview")
    assert detail["reference_contexts"] == row["references"]
    assert detail["reference_contexts"][0]["caption"] == "View"
    assert {item["original_url"] for item in row["occurrences"]} == {
        URL,
        "https://images.example/original.png",
    }


def test_rediscovery_enriches_legacy_occurrences_in_place(tmp_path: Path) -> None:
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run", [candidate()])
        row = store.records("fixture", "run")[0]
        del row["occurrences"][0]["reference_contexts"]
        with store.db:
            store._put(row)
        store.register("fixture", "run", [candidate()])
        updated = store.records("fixture", "run")[0]
    assert len(updated["occurrences"]) == 1
    assert updated["occurrences"][0]["reference_contexts"] == updated["references"]


def test_preview_requires_its_original(tmp_path: Path) -> None:
    with MediaStore(tmp_path) as store, pytest.raises(ValueError, match="original URL"):
        store.register("fixture", "run", [MediaCandidate(URL, role="preview")])


def test_bulk_registration_preserves_occurrences_with_one_write_per_url(
    tmp_path: Path,
) -> None:
    candidates = [
        MediaCandidate(
            URL,
            [] if index else [MediaReference("fixture:radar", "evidence")],
            "navigation_image" if index else "",
            page_url=f"https://example.org/page/{index}",
            caption=f"Caption {index}",
        )
        for index in range(500)
    ]
    statements: list[str] = []
    with MediaStore(tmp_path) as store:
        store.db.set_trace_callback(statements.append)
        store.register("fixture", "run", candidates)
        assert (
            sum(sql.startswith("INSERT OR REPLACE INTO media") for sql in statements)
            == 1
        )
        record = store.records("fixture", "run")[0]
        assert record["state"] == "pending"
        assert len(record["occurrences"]) == 500
        assert len(record["references"]) == 1
        store.register("fixture", "run", candidates)
        assert store.records("fixture", "run") == [record]


def test_bulk_registration_matches_sequential_state_changes(tmp_path: Path) -> None:
    reference = MediaReference("fixture:radar", "evidence")
    candidates = [
        MediaCandidate(URL, [reference], "navigation"),
        MediaCandidate(URL, [reference]),
        MediaCandidate(URL + "?other", exclusion_reason="logo"),
        MediaCandidate(URL, [reference], "unsupported_image_format"),
        MediaCandidate(URL, [reference]),
        MediaCandidate(URL, [reference], page_url="https://example.org/second"),
    ]
    with MediaStore(tmp_path / "batch") as batched:
        batched.register("fixture", "run", candidates)
        expected = batched.records("fixture", "run")
    with MediaStore(tmp_path / "sequential") as sequential:
        for candidate in candidates:
            sequential.register("fixture", "run", [candidate])
        assert sequential.records("fixture", "run") == expected


def test_rediscovery_updates_exclusion_decision_for_the_same_occurrence(
    tmp_path: Path,
) -> None:
    reference = MediaReference("fixture:radar", "evidence")
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run", [MediaCandidate(URL, [reference])])
        store.mark(
            "fixture", "run", media_id("fixture", URL), "failed", "image_too_large"
        )
        store.register(
            "fixture",
            "run",
            [MediaCandidate(URL, [reference], "declared_image_too_large")],
        )
        row = store.records("fixture", "run")[0]
        assert row["state"] == "excluded"
        assert len(row["occurrences"]) == 1
        store.register("fixture", "run", [MediaCandidate(URL, [reference])])
        assert store.records("fixture", "run")[0]["state"] == "pending"


def candidate(source: str = "fixture", url: str = URL) -> MediaCandidate:
    return MediaCandidate(
        url, [MediaReference(f"{source}:radar", "evidence-1", "Side view")]
    )


def image_file(path: Path, *, color: str = "blue", format: str = "PNG") -> Path:
    image = Image.new("RGB", (32, 24), color)
    image.save(path, format=format)
    return path


def saved(store: MediaStore, path: Path, *, archive: str = "run-1") -> dict:
    store.register("fixture", archive, [candidate()])
    return store.save(
        "fixture", archive, media_id("fixture", URL), path, content_type="image/png"
    )


def test_duplicate_bytes_keep_source_records_and_merged_references(
    tmp_path: Path,
) -> None:
    path = image_file(tmp_path / "input.png")
    with MediaStore(tmp_path) as store:
        first = saved(store, path)
        store.register(
            "fixture",
            "run-1",
            [
                candidate(),
                MediaCandidate(
                    URL, [MediaReference("fixture:radar", "evidence-2", "Front view")]
                ),
                MediaCandidate(URL, [MediaReference("fixture:radar-2", "evidence-1")]),
            ],
        )
        same = store.records("fixture", "run-1")[0]
        assert same["id"] == first["id"]
        assert same["captured_at"] == first["captured_at"]
        assert same["state"] == "saved"
        assert {ref["caption"] for ref in same["references"]} == {
            "Side view",
            "Front view",
            "",
        }
        store.register("second", "run-2", [candidate("second")])
        other = store.save(
            "second", "run-2", media_id("second", URL), path, content_type="image/png"
        )
        assert first["id"] != other["id"]
        assert first["sha256"] == other["sha256"]
        assert store.body(other["sha256"]) == path.read_bytes()
    assert len(list((tmp_path / "media" / "objects").glob("*/*"))) == 1


def test_url_identity_keeps_each_archived_version(tmp_path: Path) -> None:
    blue = image_file(tmp_path / "blue.png")
    red = image_file(tmp_path / "red.png", color="red")
    with MediaStore(tmp_path) as store:
        first = saved(store, blue)
        second = saved(store, red, archive="run-2")
        assert first["id"] == second["id"]
        assert first["sha256"] != second["sha256"]
        assert store.body(first["sha256"]) == blue.read_bytes()
        assert store.body(second["sha256"]) == red.read_bytes()
        with pytest.raises(ValueError, match="cannot change"):
            saved(store, red)


def test_states_resume_and_empty_associations_are_explicit(tmp_path: Path) -> None:
    path = image_file(tmp_path / "input.png")
    excluded = URL + "?excluded=1"
    unassociated = URL + "?unassociated=1"
    with MediaStore(tmp_path) as store:
        store.register(
            "fixture",
            "run-1",
            [
                candidate(),
                MediaCandidate(excluded, exclusion_reason="Navigation image"),
                MediaCandidate(unassociated),
            ],
        )
        store.mark(
            "fixture",
            "run-1",
            media_id("fixture", URL),
            "failed",
            "HTTP 429",
            http_status=429,
            retry_after="60",
        )
        store.register("fixture", "run-1", [candidate()])
        manifest = store.manifest("fixture", "run-1")
        assert manifest["counts"] == {
            "discovered": 3,
            "pending": 0,
            "saved": 0,
            "failed": 1,
            "excluded": 1,
            "unassociated": 1,
        }
        for url in (excluded, unassociated):
            with pytest.raises(ValueError, match="cannot be saved"):
                store.save(
                    "fixture",
                    "run-1",
                    media_id("fixture", url),
                    path,
                    content_type="image/png",
                )
        store.register("fixture", "run-1", [candidate(url=unassociated)])
        assert store.manifest("fixture", "run-1")["counts"]["pending"] == 1
        record = saved(store, path)
        assert record["error"] == ""
        assert record["retry_after"] is None


@pytest.mark.parametrize(
    "format,mime",
    [
        ("PNG", "image/png"),
        ("JPEG", "image/jpeg"),
        ("WEBP", "image/webp"),
        ("GIF", "image/gif"),
    ],
)
def test_decodes_supported_formats(tmp_path: Path, format: str, mime: str) -> None:
    path = image_file(tmp_path / "input", format=format)
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run-1", [candidate()])
        record = store.save(
            "fixture", "run-1", media_id("fixture", URL), path, content_type=mime
        )
        assert (record["width"], record["height"], record["content_type"]) == (
            32,
            24,
            mime,
        )
        assert record["bytes"] == path.stat().st_size
        assert len(record["perceptual_hash"]) == 16
        assert store.body(record["sha256"]) == path.read_bytes()
        assert "response_content_type" not in record


@pytest.mark.parametrize("mime", ["", "unknown", "application/octet-stream"])
def test_generic_content_type_uses_decoded_format(tmp_path: Path, mime: str) -> None:
    path = image_file(tmp_path / "input", format="JPEG")
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run-1", [candidate()])
        record = store.save(
            "fixture",
            "run-1",
            media_id("fixture", URL),
            path,
            content_type=mime,
            response_content_type=mime,
        )
        assert record["content_type"] == "image/jpeg"
        assert record["response_content_type"] == mime
        assert store.body(record["sha256"]) == path.read_bytes()
    with MediaStore(tmp_path, read_only=True) as store:
        assert store.records("fixture", "run-1") == [record]


def mpo_file(path: Path) -> tuple[Path, int]:
    frames = [Image.new("RGB", (24, 16), "red"), Image.new("RGB", (31, 19), "blue")]
    frames[0].save(path, format="MPO", save_all=True, append_images=frames[1:])
    with Image.open(path) as image:
        assert isinstance(image, MpoImageFile)
        image.seek(1)
        return path, image.offset


@pytest.mark.parametrize("missing_secondary", [False, True])
def test_mpo_archive_preserves_bytes_and_frame_validation(
    tmp_path: Path, missing_secondary: bool
) -> None:
    path, second_offset = mpo_file(tmp_path / "input.mpo")
    if missing_secondary:
        path.write_bytes(path.read_bytes()[:second_offset])
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run-1", [candidate()])
        record = store.save(
            "fixture",
            "run-1",
            media_id("fixture", URL),
            path,
            content_type="image/jpeg",
            response_content_type="image/jpeg",
        )
        assert record["content_type"] == "image/mpo"
        assert record["response_content_type"] == "image/jpeg"
        assert record["frame_count"] == 2
        assert record["validated_frame_count"] == (1 if missing_secondary else 2)
        assert record["unreadable_frames"] == ([1] if missing_secondary else [])
        assert (record["width"], record["height"]) == (24, 16)
        assert store.body(record["sha256"]) == path.read_bytes()
    with MediaStore(tmp_path, read_only=True) as store:
        assert store.records("fixture", "run-1") == [record]


@pytest.mark.parametrize("failure", ["primary", "secondary", "pixels", "frames"])
def test_mpo_rejects_corrupt_present_frames_and_aggregate_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    path, second_offset = mpo_file(tmp_path / "input.mpo")
    if failure == "primary":
        path.write_bytes(path.read_bytes()[: second_offset - 20])
    elif failure == "secondary":
        path.write_bytes(path.read_bytes()[:-20])
    elif failure == "pixels":
        monkeypatch.setattr("pipelines.media.MAX_IMAGE_PIXELS", 24 * 16 + 31 * 19 - 1)
    else:
        monkeypatch.setattr("pipelines.media.MAX_IMAGE_FRAMES", 1)
    expected = {
        "primary": "Invalid or truncated",
        "secondary": "Invalid or truncated",
        "pixels": "aggregate",
        "frames": "frame limit",
    }[failure]
    with MediaStore(tmp_path) as store, pytest.raises(ValueError, match=expected):
        saved(store, path)
    assert not (tmp_path / "media" / "objects").exists()


def test_rejects_unsupported_mime_truncation_animation_and_limits(
    tmp_path: Path,
) -> None:
    path = image_file(tmp_path / "input.png")
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run-1", [candidate()])
        with pytest.raises(ValueError, match="MIME"):
            store.save(
                "fixture",
                "run-1",
                media_id("fixture", URL),
                path,
                content_type="image/svg+xml",
            )
        path.write_bytes(path.read_bytes()[:40])
        with pytest.raises(ValueError, match="Invalid or truncated"):
            saved(store, path)
        frames = [Image.new("RGB", (2, 2), color) for color in ("red", "blue")]
        frames[0].save(path, format="PNG", save_all=True, append_images=frames[1:])
        with pytest.raises(ValueError, match="Animated"):
            saved(store, path)
        Image.new("1", (6400, 6300)).save(path, format="PNG")
        with pytest.raises(ValueError, match="pixel limit"):
            saved(store, path)
        with path.open("wb") as handle:
            handle.truncate(MAX_IMAGE_BYTES + 1)
        with pytest.raises(ValueError, match="byte limit"):
            saved(store, path)
        assert store.manifest("fixture", "run-1")["counts"]["saved"] == 0
    assert not (tmp_path / "media" / "objects").exists()


def test_corruption_is_rejected_and_verified_recapture_repairs_blob(
    tmp_path: Path,
) -> None:
    path = image_file(tmp_path / "input.png")
    with MediaStore(tmp_path) as store:
        record = saved(store, path)
        with pytest.raises(ValueError, match="cannot be marked failed"):
            store.mark("fixture", "run-1", record["id"], "failed", "corruption")
        digest = record["sha256"]
        (tmp_path / "media" / "objects" / digest[:2] / digest).write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="checksum"):
            store.body(digest)
        store.mark("fixture", "run-1", record["id"], "failed", "checksum mismatch")
        different = image_file(tmp_path / "changed.png", color="red")
        with pytest.raises(ValueError, match="cannot change"):
            saved(store, different)
        saved(store, path)
        assert store.body(digest) == path.read_bytes()


def test_near_duplicates_are_hints_and_originals_remain_distinct(
    tmp_path: Path,
) -> None:
    image = Image.new("RGB", (72, 64))
    image.putdata([(x * 3, y * 3, (x + y) * 2) for y in range(64) for x in range(72)])
    original = tmp_path / "view.png"
    rotated = tmp_path / "exif.jpg"
    image.save(original)
    exif = Image.Exif()
    exif[274] = 6
    image.transpose(Image.Transpose.ROTATE_90).save(rotated, quality=95, exif=exif)
    other_url = URL + "?view=2"
    with MediaStore(tmp_path) as store:
        first = saved(store, original)
        store.register("fixture", "run-1", [candidate(url=other_url)])
        other = store.save(
            "fixture",
            "run-1",
            media_id("fixture", other_url),
            rotated,
            content_type="image/jpeg",
        )
        assert (other["width"], other["height"]) == (72, 64)
        report = store.manifest("fixture", "run-1")
        assert report["counts"]["saved"] == 2
        assert report["exact_duplicates"] == []
        assert len(report["near_duplicates"]) == 1
        assert report["near_duplicates"][0]["distance"] <= 5
        assert store.body(first["sha256"]) != store.body(other["sha256"])
        store.register("second", "run-2", [candidate("second")])
        store.save(
            "second",
            "run-2",
            media_id("second", URL),
            rotated,
            content_type="image/jpeg",
        )
        global_report = store.manifest("second", "run-2")
        assert global_report["counts"]["saved"] == 1
        hint = global_report["near_duplicates"][0]
        assert {
            ref["source"] for ref in hint["left_records"] + hint["right_records"]
        } == {"fixture", "second"}


def test_recipes_cache_offline_and_invalidate_on_each_input(tmp_path: Path) -> None:
    path = image_file(tmp_path / "input.png")
    calls: list[Path] = []

    def produce(original: Path, output: Path) -> None:
        calls.append(output)
        with Image.open(original) as image:
            image.resize((8, 8)).save(output, format="PNG")

    recipe = ProcessingRecipe("preview-v1", "model-commit", {"size": 8, "color": "RGB"})
    with MediaStore(tmp_path) as store:
        record = saved(store, path)
        digest = record["sha256"]
        target = store.derive(digest, recipe, produce)
    path.unlink()
    with MediaStore(tmp_path) as store:
        assert store.derive(digest, recipe, produce) == target
        assert len(calls) == 1
        ordered = ProcessingRecipe(
            "preview-v1", "model-commit", {"color": "RGB", "size": 8}
        )
        assert recipe_key(digest, recipe) == recipe_key(digest, ordered)
        variants = [
            ProcessingRecipe("preview-v2", "model-commit", recipe.settings),
            ProcessingRecipe("preview-v1", "new-model-commit", recipe.settings),
            ProcessingRecipe("preview-v1", "model-commit", {"size": 12}),
            ProcessingRecipe(
                "preview-v1", "model-commit", recipe.settings, model_id="another-model"
            ),
        ]
        for variant in variants:
            assert store.derive(digest, variant, produce) != target
        assert len(calls) == 5
        different = hashlib.sha256(b"different original").hexdigest()
        assert recipe_key(different, recipe) != recipe_key(digest, recipe)
        target.write_bytes(b"corrupted")
        with pytest.raises(ValueError, match="checksum"):
            store.derive(digest, recipe, produce)
        assert len(calls) == 5
    assert list((tmp_path / "media" / "tmp").iterdir()) == []


def test_failed_recipe_leaves_no_partial_outputs(tmp_path: Path) -> None:
    path = image_file(tmp_path / "input.png")
    recipe = ProcessingRecipe("preview-v1")

    def fail(original: Path, output: Path) -> None:
        original.write_bytes(b"producer modifies its temporary input")
        output.write_bytes(b"partial")
        raise RuntimeError("analysis interrupted")

    with MediaStore(tmp_path) as store:
        record = saved(store, path)
        with pytest.raises(RuntimeError, match="interrupted"):
            store.derive(record["sha256"], recipe, fail)
        assert store.body(record["sha256"]) == path.read_bytes()
    assert not (tmp_path / "media" / "derived").exists()
    assert list((tmp_path / "media" / "tmp").iterdir()) == []


def test_concurrent_stores_reuse_one_complete_derived_artifact(tmp_path: Path) -> None:
    path = image_file(tmp_path / "input.png")
    with MediaStore(tmp_path) as store:
        digest = saved(store, path)["sha256"]
    calls: list[bool] = []

    def produce(original: Path, output: Path) -> None:
        calls.append(True)
        output.write_bytes(original.read_bytes())

    def derive(_: int) -> bytes:
        with MediaStore(tmp_path) as store:
            return store.derive(
                digest, ProcessingRecipe("copy-v1"), produce
            ).read_bytes()

    with ThreadPoolExecutor(max_workers=3) as pool:
        outputs = list(pool.map(derive, range(3)))
    assert outputs == [path.read_bytes()] * 3
    assert calls == [True]


@pytest.mark.skipif(os.name != "nt", reason="Windows extended-path prefix")
def test_concurrent_directory_path_prefix_does_not_change_archive_scope(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = image_file(tmp_path / "input.png")
    resolve = Path.resolve

    def extended(path: Path, strict: bool = False) -> Path:
        resolved = resolve(path, strict=strict)
        if "locks" in path.parts and not str(resolved).startswith("\\\\?\\"):
            return Path("\\\\?\\" + str(resolved))
        return resolved

    def produce(original: Path, output: Path) -> None:
        output.write_bytes(original.read_bytes())

    with MediaStore(tmp_path) as store:
        digest = saved(store, path)["sha256"]
        monkeypatch.setattr(Path, "resolve", extended)
        result = store.derive(digest, ProcessingRecipe("copy-v1"), produce)
        assert result.read_bytes() == path.read_bytes()


def test_read_only_does_not_create_and_versions_are_validated(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    with pytest.raises(sqlite3.OperationalError):
        MediaStore(missing, read_only=True)
    assert not missing.exists()
    with MediaStore(tmp_path) as store:
        store.register("fixture", "run-1", [candidate()])
    with MediaStore(tmp_path, read_only=True) as store:
        assert store.manifest("fixture", "run-1")["counts"]["pending"] == 1
        with pytest.raises(ValueError, match="read-only"):
            store.register("fixture", "run-2", [])
    db_path = tmp_path / "media" / "manifest.sqlite"
    with sqlite3.connect(db_path) as db:
        record = json.loads(db.execute("SELECT data FROM media").fetchone()[0])
        record["schema_version"] = 999
        db.execute("UPDATE media SET data=?", (json.dumps(record),))
    with MediaStore(tmp_path, read_only=True) as store:
        with pytest.raises(ValueError, match="record version"):
            store.records("fixture", "run-1")
    with sqlite3.connect(db_path) as db:
        db.execute("PRAGMA user_version=999")
    with pytest.raises(ValueError, match="schema version"):
        MediaStore(tmp_path)
    with sqlite3.connect(db_path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_invalid_ids_urls_and_recipes_are_rejected(tmp_path: Path) -> None:
    for url in (
        "file:///picture.png",
        "https://user:secret@example/image.png",
        URL + "#fragment",
    ):
        with pytest.raises(ValueError, match="HTTP"):
            media_id("fixture", url)
    with MediaStore(tmp_path) as store:
        for source, archive in (("../outside", "run"), ("fixture", "../outside")):
            with pytest.raises(ValueError, match="safe keys"):
                store.register(source, archive, [candidate()])
        with pytest.raises(ValueError, match="source-qualified"):
            store.register("fixture", "run", [candidate("other")])
        with pytest.raises(ValueError, match="SHA-256"):
            store.body("../../outside")
    with pytest.raises(ValueError, match="version"):
        recipe_key("a" * 64, ProcessingRecipe(""))
    with pytest.raises(ValueError, match="JSON compliant"):
        recipe_key(
            "a" * 64, ProcessingRecipe("v1", settings={"threshold": float("nan")})
        )
