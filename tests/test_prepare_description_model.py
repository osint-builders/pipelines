import hashlib
import json
from io import BytesIO
from pathlib import Path

import prepare_description_model as prepare
import pytest


def fixture_lock(tmp_path: Path) -> tuple[Path, dict[str, bytes]]:
    manifest = json.loads(prepare.LOCK.read_text())
    bodies = {entry["file"]: entry["file"].encode() for entry in manifest["files"]}
    for entry in manifest["files"]:
        entry.update(
            bytes=len(bodies[entry["file"]]),
            sha256=hashlib.sha256(bodies[entry["file"]]).hexdigest(),
        )
    lock = tmp_path / "lock.json"
    lock.write_text(json.dumps(manifest))
    return lock, bodies


def test_preparation_requires_explicit_download_and_reuses_verified_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, bodies = fixture_lock(tmp_path)
    directory = tmp_path / "model"
    with pytest.raises(ValueError, match="--download"):
        prepare.prepare(directory, lock=lock)
    calls = []

    def fetch(url: str, **kwargs: object) -> BytesIO:
        calls.append(url)
        assert "/89644892e4d85e24eaac8bacfd4f463576704203/" in url
        return BytesIO(bodies[url.rsplit("/", 1)[-1]])

    monkeypatch.setattr(prepare.urllib.request, "urlopen", fetch)
    output = prepare.prepare(directory, download=True, lock=lock)
    assert json.loads(output.read_text()) == json.loads(lock.read_text())
    assert len(calls) == len(bodies)
    prepare.prepare(directory, lock=lock)
    assert len(calls) == len(bodies)


def test_preparation_rejects_corrupt_download_without_publishing_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lock, bodies = fixture_lock(tmp_path)
    directory = tmp_path / "model"
    monkeypatch.setattr(
        prepare.urllib.request,
        "urlopen",
        lambda *a, **k: BytesIO(b"x" * len(bodies["chat_template.json"])),
    )
    with pytest.raises(ValueError, match="checksum"):
        prepare.prepare(directory, download=True, lock=lock)
    assert not list(directory.iterdir())
