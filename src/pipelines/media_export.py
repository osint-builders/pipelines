"""Export original media bound to an image-enabled CLI dataset."""

import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path

from filelock import FileLock

from pipelines.archive import atomic_json
from pipelines.distribution import sha256
from pipelines.media import MAX_IMAGE_BYTES, MediaStore

MAX_ARCHIVE_BYTES = 1_500_000_000


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def media_assets(directory: Path, dataset: dict) -> list[str]:
    report = json.loads((directory / "image-dataset.json").read_bytes())
    records_path = directory / "image-records.json"
    if (
        report.get("schema_version") != 1
        or report.get("dataset_id") != dataset["dataset_id"]
        or report.get("records") != dataset.get("image", {}).get("records")
        or report.get("gallery_sha256")
        != dataset.get("image", {}).get("gallery_sha256")
        or file_sha256(records_path) != report["gallery_sha256"]
    ):
        raise ValueError("Original image export differs from the CLI dataset")
    records = json.loads(records_path.read_bytes())
    expected = {r["sha256"] for r in records}
    actual: list[str] = []
    assets = ["image-dataset.json", "image-records.json"]
    for part in report["archives"]:
        name = part["name"]
        if not re.fullmatch(r"images-[0-9]{3,}\.zip", name) or name in assets:
            raise ValueError("Invalid or duplicate image archive name")
        path = directory / name
        if path.stat().st_size != part["bytes"] or file_sha256(path) != part["sha256"]:
            raise ValueError("Image archive checksum mismatch")
        with zipfile.ZipFile(path) as archive:
            expected_members = [
                f"objects/{digest[:2]}/{digest}" for digest in part["objects"]
            ]
            if archive.namelist() != expected_members:
                raise ValueError("Image archive members differ from the manifest")
            for member, digest in zip(expected_members, part["objects"], strict=True):
                if archive.getinfo(member).file_size > MAX_IMAGE_BYTES:
                    raise ValueError("Exported original exceeds the image size limit")
                with archive.open(member) as stream:
                    actual_digest = hashlib.sha256()
                    while block := stream.read(1024 * 1024):
                        actual_digest.update(block)
                    if actual_digest.hexdigest() != digest:
                        raise ValueError("Original image checksum mismatch")
        actual.extend(part["objects"])
        assets.append(name)
    if (
        set(actual) != expected
        or len(actual) != len(expected)
        or report["objects"] != len(expected)
    ):
        raise ValueError("Image export has missing or duplicate originals")
    return assets


def export_media(
    root: Path,
    bundle: Path,
    output: Path,
    *,
    max_archive_bytes: int = MAX_ARCHIVE_BYTES,
) -> dict:
    if (
        type(max_archive_bytes) is not int
        or not 1024 <= max_archive_bytes <= MAX_ARCHIVE_BYTES
    ):
        raise ValueError("Invalid image archive size limit")
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        records_body = archive.read("image/index.json")
        if (
            sha256(records_body) != manifest["files"].get("image/index.json")
            or sha256(records_body) != manifest["image"]["gallery_sha256"]
        ):
            raise ValueError("Image index checksum mismatch")
        records = json.loads(records_body)
        if len(records) != manifest["image"]["records"]:
            raise ValueError("Image index count mismatch")
    output.mkdir(parents=True, exist_ok=True)
    with FileLock(output / "images.lock", timeout=0):
        report_path = output / "image-dataset.json"
        if report_path.exists():
            media_assets(output, manifest)
            return json.loads(report_path.read_bytes())
        hashes = sorted({row["sha256"] for row in records})
        parts: list[dict] = []
        part: dict = {}
        writer: zipfile.ZipFile | None = None
        temporary = output / "images.tmp"
        estimated_bytes = 22
        original_bytes = 0

        def finish() -> None:
            nonlocal writer
            if writer is None:
                return
            writer.close()
            writer = None
            if temporary.stat().st_size > max_archive_bytes:
                raise ValueError("Image archive exceeds the size limit")
            destination = output / part["name"]
            temporary.replace(destination)
            part.update(
                bytes=destination.stat().st_size, sha256=file_sha256(destination)
            )
            parts.append(part)

        try:
            with MediaStore(root, read_only=True) as store:
                for digest in hashes:
                    body = store.body(digest)
                    member = f"objects/{digest[:2]}/{digest}"
                    required = len(body) + 76 + 2 * len(member)
                    if required + 1024 > max_archive_bytes:
                        raise ValueError("One image exceeds the archive size limit")
                    if (
                        writer is not None
                        and estimated_bytes + required + 1024 > max_archive_bytes
                    ):
                        finish()
                    if writer is None:
                        part = {
                            "name": f"images-{len(parts) + 1:03d}.zip",
                            "objects": [],
                        }
                        writer = zipfile.ZipFile(
                            temporary, "w", compression=zipfile.ZIP_STORED
                        )
                        estimated_bytes = 22
                    info = zipfile.ZipInfo(member, date_time=(1980, 1, 1, 0, 0, 0))
                    info.external_attr = 0o100644 << 16
                    writer.writestr(info, body)
                    part["objects"].append(digest)
                    estimated_bytes += required
                    original_bytes += len(body)
                finish()
        finally:
            if writer is not None:
                writer.close()
        records_path = output / "image-records.json"
        records_path.write_bytes(records_body)
        report = {
            "schema_version": 1,
            "dataset_id": manifest["dataset_id"],
            "gallery_sha256": manifest["image"]["gallery_sha256"],
            "records": len(records),
            "objects": len(hashes),
            "original_bytes": original_bytes,
            "sources": dict(sorted(Counter(row["source"] for row in records).items())),
            "archives": parts,
        }
        atomic_json(report_path, report)
        media_assets(output, manifest)
        return report
