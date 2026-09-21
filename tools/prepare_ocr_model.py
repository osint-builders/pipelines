"""Prepare the pinned OCR models for local build-time analysis."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

from pipelines.ocr import LOCK_PATH, verify_models


def prepare(directory: Path, *, download: bool = False) -> Path:
    manifest = json.loads(LOCK_PATH.read_bytes())
    directory.mkdir(parents=True, exist_ok=True)
    for item in manifest["models"].values():
        target = directory / item["file"]
        if (
            target.is_file()
            and target.stat().st_size == item["bytes"]
            and hashlib.sha256(target.read_bytes()).hexdigest() == item["sha256"]
        ):
            continue
        if not download:
            raise ValueError(
                f"Missing or invalid {item['file']}; use --download to fetch it"
            )
        temporary = target.with_suffix(".tmp")
        try:
            digest = hashlib.sha256()
            size = 0
            with (
                urllib.request.urlopen(item["url"], timeout=60) as response,
                temporary.open("wb") as output,
            ):
                while body := response.read(1024 * 1024):
                    size += len(body)
                    if size > item["bytes"]:
                        raise ValueError("OCR model download exceeds its pinned size")
                    digest.update(body)
                    output.write(body)
            if size != item["bytes"] or digest.hexdigest() != item["sha256"]:
                raise ValueError("OCR model download checksum mismatch")
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    verify_models(directory, manifest)
    path = directory / "ocr.json"
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(manifest, sort_keys=True, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, default=Path("build/models/ocr"))
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(prepare(args.directory, download=args.download))


if __name__ == "__main__":
    main()
