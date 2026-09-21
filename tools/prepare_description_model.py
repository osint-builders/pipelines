"""Prepare pinned local Qwen files; model inference never downloads weights."""

import argparse
import hashlib
import json
import tempfile
import urllib.request
from pathlib import Path

from pipelines.description import validate_manifest, verify_files

LOCK = Path(__file__).resolve().parents[1] / "src/pipelines/description_model.lock.json"


def prepare(directory: Path, *, download: bool = False, lock: Path = LOCK) -> Path:
    manifest = json.loads(lock.read_text(encoding="utf-8"))
    validate_manifest(manifest)
    directory.mkdir(parents=True, exist_ok=True)
    directory = directory.resolve()
    for entry in manifest["files"]:
        path = (directory / entry["file"]).resolve()
        if not path.is_relative_to(directory):
            raise ValueError("Description model file escapes directory")
        if path.exists():
            verify_files(directory, {"files": [entry]})
            continue
        if not download:
            raise ValueError(f"Missing {entry['file']}; use --download to fetch it")
        url = (
            f"https://huggingface.co/{manifest['model_id']}/resolve/"
            f"{manifest['revision']}/{entry['file']}"
        )
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=directory, suffix=".part", delete=False
            ) as stream:
                temporary = Path(stream.name)
                digest = hashlib.sha256()
                with urllib.request.urlopen(url, timeout=60) as response:
                    while body := response.read(1024 * 1024):
                        stream.write(body)
                        digest.update(body)
                        if stream.tell() > entry["bytes"]:
                            raise ValueError("Description download exceeds pinned size")
                if (
                    stream.tell() != entry["bytes"]
                    or digest.hexdigest() != entry["sha256"]
                ):
                    raise ValueError("Description download checksum mismatch")
            temporary.replace(path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    verify_files(directory, manifest)
    output = directory / "description-model.json"
    output.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return output


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    print(prepare(args.output, download=args.download))


if __name__ == "__main__":
    main()
