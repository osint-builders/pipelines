import json
import zipfile
from copy import deepcopy
from io import BytesIO
from pathlib import Path

import pytest
from test_pipeline import HTML, archived

from pipelines.build import publish
from pipelines.distribution import (
    LOCK,
    _cached_vectors,
    canonical,
    collect_artifacts,
    content_digest,
    sha256,
    validate_calibration_bundle,
    write_bundle,
)


def test_bundled_vector_reuse_requires_exact_chunks_model_and_checksums(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "dataset.zip"
    chunks = [{"text": "first"}, {"text": "second"}]
    vectors = b"\x01" * (len(chunks) * LOCK["dimensions"] * 4)
    manifest = {
        "model": LOCK,
        "files": {
            "chunks.json": sha256(canonical(chunks)),
            "vectors.f32": sha256(vectors),
        },
    }

    def write(value: dict, body: bytes = vectors) -> None:
        write_bundle(
            bundle,
            {
                "manifest.json": canonical(value),
                "chunks.json": canonical(chunks),
                "vectors.f32": body,
            },
        )

    assert _cached_vectors(bundle, chunks) is None
    write(manifest)
    assert _cached_vectors(bundle, chunks) == vectors
    assert _cached_vectors(bundle, list(reversed(chunks))) is None
    write({**manifest, "model": {}})
    assert _cached_vectors(bundle, chunks) is None
    write(manifest, vectors[:-4])
    with pytest.raises(ValueError, match="vector checksum or size"):
        _cached_vectors(bundle, chunks)
    changed = deepcopy(manifest)
    changed["files"]["vectors.f32"] = sha256(vectors[:-4])
    write(changed, vectors[:-4])
    with pytest.raises(ValueError, match="vector checksum or size"):
        _cached_vectors(bundle, chunks)
    changed["files"]["chunks.json"] = "0" * 64
    write(changed)
    with pytest.raises(ValueError, match="chunk checksum"):
        _cached_vectors(bundle, chunks)


def test_export_preserves_full_content_and_stable_source_id(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    try:
        snapshot = publish(source, archive, source_dir)
    finally:
        archive.close()
    entities, bodies, _ = collect_artifacts(tmp_path, [source.id])
    document = entities[0]
    assert document["id"] == f"{source.id}:{document['source_id']}"
    assert bodies[document["source"] + "/" + document["evidence"][0]["id"]] == HTML
    assert document["evidence"][0]["markdown"] == (
        snapshot / "markdown" / f"{document['evidence'][0]['id']}.md"
    ).read_text(encoding="utf-8")
    assert document["facts"][0]["evidence"].startswith(document["url"])


def test_corrupt_archive_cannot_be_distributed(tmp_path: Path) -> None:
    source, archive, source_dir = archived(tmp_path)
    publish(source, archive, source_dir)
    page = archive.pages("saved")[0]
    (archive.path / page["file"]).write_bytes(b"changed after publication")
    archive.close()
    with pytest.raises(ValueError, match="checksum"):
        collect_artifacts(tmp_path, [source.id])


def test_digest_ignores_capture_metadata_but_detects_content_edits_and_deletions() -> (
    None
):
    original = [
        {
            "id": "one",
            "title": "Radar",
            "html_sha256": "abc",
            "retrieved_at": "yesterday",
        },
        {"id": "two"},
    ]
    later = [{**original[0], "retrieved_at": "today"}, original[1]]
    assert content_digest(original) == content_digest(later)
    assert content_digest(original) != content_digest(
        [{**original[0], "title": "New radar"}, original[1]]
    )
    assert content_digest(original) != content_digest(original[:1])
    assert content_digest(original) == content_digest(
        [{**original[0], "html_sha256": "def"}, original[1]]
    )


def test_bundle_is_deterministic_and_preserves_binary_payload(tmp_path: Path) -> None:
    members = {
        "html/page.html": b"\xff\x00\r\n",
        "manifest.json": json.dumps({"entities": 1}).encode(),
    }
    first, second = tmp_path / "one.zip", tmp_path / "two.zip"
    write_bundle(first, members)
    write_bundle(second, dict(reversed(list(members.items()))))
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.read("html/page.html") == members["html/page.html"]


def test_runtime_binary_members_need_no_zip_inflation(tmp_path: Path) -> None:
    members = {
        "model/model.onnx": b"graph" * 100,
        "image/image.onnx": b"image graph" * 100,
        "vectors.f32": b"vectors" * 100,
        "image/vectors.f16": b"image vectors" * 100,
        "observations/vectors.f32": b"observation vectors" * 100,
        "entities/source/entity.json": b'{"title":"radar"}',
    }
    output = tmp_path / "runtime.zip"
    write_bundle(output, members)
    with zipfile.ZipFile(output) as archive:
        for name, body in members.items():
            assert archive.read(name) == body
            expected = (
                zipfile.ZIP_DEFLATED if name.endswith(".json") else zipfile.ZIP_STORED
            )
            assert archive.getinfo(name).compress_type == expected


@pytest.mark.parametrize(
    "mutation",
    ["format", "checksum", "count", "boolean_schema", "retrieval", "missing"],
)
def test_calibration_extension_rejects_stale_or_incomplete_metadata(
    mutation: str,
) -> None:
    from pipelines.calibration import canonical, digest

    fixture = json.loads(Path("tests/fixtures/calibration.json").read_bytes())
    manifest = deepcopy(fixture["binding"]["manifest"])
    artifact = fixture["golden"][0]["artifact"]
    body = canonical(artifact)
    manifest["format_version"] = 5
    manifest["files"]["calibration.json"] = digest(body)
    manifest["calibration"] = {
        "member": "calibration.json",
        "sha256": digest(body),
        "schema_version": 1,
        "profiles": len(artifact["profiles"]),
        "retrieval_sha256": artifact["retrieval_sha256"],
    }
    data = BytesIO()
    with zipfile.ZipFile(data, "w") as archive:
        archive.writestr("calibration.json", body)
    with zipfile.ZipFile(data) as archive:
        validate_calibration_bundle(archive, manifest)
        if mutation == "format":
            manifest["format_version"] = 4
        elif mutation == "checksum":
            manifest["calibration"]["sha256"] = "0" * 64
        elif mutation == "count":
            manifest["calibration"]["profiles"] += 1
        elif mutation == "boolean_schema":
            manifest["calibration"]["schema_version"] = True
        elif mutation == "retrieval":
            manifest["search"]["lexical_weight"] += 0.5
        else:
            manifest.pop("calibration")
        with pytest.raises(ValueError):
            validate_calibration_bundle(archive, manifest)


def test_legacy_bundle_rejects_null_calibration_declaration() -> None:
    data = BytesIO()
    with zipfile.ZipFile(data, "w"):
        pass
    with zipfile.ZipFile(data) as archive:
        with pytest.raises(ValueError, match="format 5"):
            validate_calibration_bundle(
                archive, {"format_version": 4, "files": {}, "calibration": None}
            )
