import zipfile
from pathlib import Path

import pytest
import release
from PIL import Image

from pipelines.distribution import canonical, sha256
from pipelines.media import MediaCandidate, MediaReference, MediaStore, media_id
from pipelines.media_export import export_media, file_sha256, media_assets


@pytest.fixture
def image_dataset(tmp_path: Path) -> tuple[Path, Path, dict]:
    root = tmp_path / "archive"
    rows = []
    with MediaStore(root) as store:
        for i, color in enumerate(["red", "blue", "red"]):
            path = tmp_path / "image.png"
            Image.new("RGB", (16, 12), color).save(path)
            url = f"https://example.test/{i}.png"
            store.register(
                "fixture",
                "capture",
                [MediaCandidate(url, [MediaReference(f"fixture:{i}", "page")])],
            )
            row = store.save(
                "fixture",
                "capture",
                media_id("fixture", url),
                path,
                content_type="image/png",
            )
            rows.append(
                {
                    k: row[k]
                    for k in (
                        "id",
                        "source",
                        "url",
                        "sha256",
                        "references",
                        "content_type",
                    )
                }
            )
    raw = canonical(rows)
    manifest = {
        "dataset_id": "a" * 64,
        "files": {"image/index.json": sha256(raw)},
        "image": {"records": len(rows), "gallery_sha256": sha256(raw)},
    }
    bundle = tmp_path / "dataset.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr("manifest.json", canonical(manifest))
        archive.writestr("image/index.json", raw)
    return root, bundle, manifest


def test_original_export_is_complete_deduplicated_bounded_and_deterministic(
    image_dataset: tuple[Path, Path, dict], tmp_path: Path
) -> None:
    root, bundle, manifest = image_dataset
    first, second = tmp_path / "first", tmp_path / "second"
    report = export_media(root, bundle, first, max_archive_bytes=1500)
    assert report == export_media(root, bundle, second, max_archive_bytes=1500)
    assert report == export_media(root, bundle, first, max_archive_bytes=1500)
    assert report["records"] == 3 and report["objects"] == 2
    assert len(report["archives"]) == 2 and report["sources"] == {"fixture": 3}
    assets = media_assets(first, manifest)
    assert {p["name"] for p in report["archives"]}.issubset(assets)
    for name in assets:
        assert (first / name).read_bytes() == (second / name).read_bytes()
    with MediaStore(root, read_only=True) as store:
        for part in report["archives"]:
            path = first / part["name"]
            assert path.stat().st_size <= 1500
            with zipfile.ZipFile(path) as archive:
                for digest in part["objects"]:
                    assert archive.read(f"objects/{digest[:2]}/{digest}") == store.body(
                        digest
                    )


def test_export_rejects_changed_binding_and_corrupted_original(
    image_dataset: tuple[Path, Path, dict], tmp_path: Path
) -> None:
    root, bundle, manifest = image_dataset
    output = tmp_path / "export"
    report = export_media(root, bundle, output)
    with pytest.raises(ValueError, match="differs"):
        media_assets(output, {**manifest, "dataset_id": "b" * 64})
    part = output / report["archives"][0]["name"]
    part.write_bytes(part.read_bytes() + b"corruption")
    with pytest.raises(ValueError, match="checksum"):
        export_media(root, bundle, output)
    digest = report["archives"][0]["objects"][0]
    (root / "media/objects" / digest[:2] / digest).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        export_media(root, bundle, tmp_path / "another")


def test_export_checks_zip_members_even_when_archive_digest_is_updated(
    image_dataset: tuple[Path, Path, dict], tmp_path: Path
) -> None:
    root, bundle, manifest = image_dataset
    output = tmp_path / "export"
    report = export_media(root, bundle, output)
    part = report["archives"][0]
    path = output / part["name"]
    with zipfile.ZipFile(path, "w") as archive:
        for digest in part["objects"]:
            archive.writestr(f"objects/{digest[:2]}/{digest}", b"wrong original")
    part.update(bytes=path.stat().st_size, sha256=file_sha256(path))
    (output / "image-dataset.json").write_bytes(canonical(report))
    with pytest.raises(ValueError, match="Original image checksum"):
        media_assets(output, manifest)


def test_cli_release_omits_original_images_and_build_evidence(
    image_dataset: tuple[Path, Path, dict], tmp_path: Path
) -> None:
    root, bundle, manifest = image_dataset
    output = tmp_path / "release"
    export_media(root, bundle, output)
    (output / "dataset-manifest.json").write_bytes(canonical(manifest))
    for _, _, name in release.BINARY_TARGETS:
        (output / name).write_bytes(b"fixture executable")
    for name in ("quality.json", "quality-evidence.zip", "validation-linux-amd64.json"):
        (output / name).write_bytes(b"retained build evidence")
    assets = release.prepare_assets(output)
    expected = {
        release.archive_name(system, name) for system, _, name in release.BINARY_TARGETS
    } | {"SHA256SUMS"}
    assert set(assets) == expected
    assert set(media_assets(output, manifest)).isdisjoint(assets)
    assert (output / "quality-evidence.zip").read_bytes() == b"retained build evidence"
    checksums = dict(
        line.split("  ", 1)[::-1]
        for line in (output / "SHA256SUMS").read_text().splitlines()
    )
    assert set(checksums) == set(assets) - {"SHA256SUMS"}
    assert all(
        file_sha256(output / name) == digest for name, digest in checksums.items()
    )
