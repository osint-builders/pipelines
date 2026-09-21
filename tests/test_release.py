import json
import subprocess
from pathlib import Path

import pytest
import release


def test_release_gate_requires_content_change_not_code_or_model_change() -> None:
    previous = {"content_sha256": "same", "recipe_sha256": "old", "dataset_id": "old"}
    assert not release.changed(
        {**previous, "recipe_sha256": "new", "dataset_id": "new"}, previous
    )
    assert release.changed({**previous, "content_sha256": "changed"}, previous)
    assert release.changed(previous, None)


def test_release_gate_does_not_treat_authentication_failure_as_first_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "HTTP 403 forbidden"),
    )
    with pytest.raises(RuntimeError, match="403"):
        release.latest_manifest("owner/repo", tmp_path)


def test_release_gate_validates_input_tag_before_network(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def forbidden(*args: str) -> str:
        raise AssertionError("network call")

    monkeypatch.setattr(release, "gh", forbidden)
    with pytest.raises(ValueError, match="Input tag"):
        release.gate("owner/repo", "arbitrary-release", tmp_path)


def test_unchanged_content_cannot_upload_an_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = {"content_sha256": "same"}
    monkeypatch.setattr(release, "verify_bundle", lambda path: manifest)
    monkeypatch.setattr(release, "latest_manifest", lambda repo, path: manifest)

    def forbidden(*args: str) -> str:
        raise AssertionError("upload was attempted")

    monkeypatch.setattr(release, "gh", forbidden)
    release.stage("owner/repo", tmp_path / "unused.zip")


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("local", [True, False])
def test_publication_waits_for_complete_draft_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, complete: bool, local: bool
) -> None:
    manifest: dict = {
        "content_sha256": "a" * 64,
        "dataset_id": "b" * 64,
        "entities": 2,
    }
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest))
    names = [
        "pipelines-linux-amd64",
        "pipelines-linux-arm64",
        "pipelines-darwin-amd64",
        "pipelines-darwin-arm64",
        "pipelines-windows-amd64.exe",
    ]
    for name in names:
        (tmp_path / name).write_bytes(b"test binary")
    calls: list[tuple[str, ...]] = []

    def fake_gh(*args: str) -> str:
        calls.append(args)
        if args[:2] == ("release", "view"):
            uploaded = [
                *(
                    release.archive_name(system, name)
                    for system, _, name in release.BINARY_TARGETS
                ),
                "dataset-manifest.json",
                "SHA256SUMS",
            ]
            if not complete:
                uploaded.pop()
            return json.dumps(
                {
                    "isDraft": True,
                    "assets": [
                        {"name": name, "size": (tmp_path / name).stat().st_size}
                        for name in uploaded
                    ],
                }
            )
        return ""

    if local:
        monkeypatch.delenv("GITHUB_SHA", raising=False)
        notes = tmp_path / "manual-notes.md"
        notes.write_text("Manually built; native macOS acceptance pending.")
    else:
        monkeypatch.setenv("GITHUB_SHA", "c" * 40)
        notes = None
    monkeypatch.setattr(release, "latest_manifest", lambda repo, path: None)
    monkeypatch.setattr(release, "gh", fake_gh)
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "HTTP 404"),
    )
    if complete:
        release.publish(
            "owner/repo",
            tmp_path,
            "cli-" + manifest["dataset_id"],
            target="d" * 40 if local else None,
            notes_file=notes,
        )
        assert calls[-1][:2] == ("release", "edit")
        assert "--draft=false" in calls[-1]
    else:
        with pytest.raises(ValueError, match="incomplete"):
            release.publish(
                "owner/repo",
                tmp_path,
                "cli-" + manifest["dataset_id"],
                target="d" * 40 if local else None,
                notes_file=notes,
            )
        assert not any(call[:2] == ("release", "edit") for call in calls)
    assert calls[0][:2] == ("release", "create")
    assert "--draft" in calls[0] and "--latest" not in calls[0]
    assert calls[0][calls[0].index("--target") + 1] == ("d" if local else "c") * 40
    if notes:
        assert notes.read_text() == "Manually built; native macOS acceptance pending."
        assert calls[0][calls[0].index("--notes-file") + 1] == str(notes)


@pytest.mark.parametrize("system", ["linux", "windows"])
def test_release_archive_preserves_one_binary_and_rebuilds_stale_content(
    tmp_path: Path, system: str
) -> None:
    name = (
        "pipelines-windows-amd64.exe"
        if system == "windows"
        else "pipelines-linux-amd64"
    )
    binary = tmp_path / name
    binary.write_bytes(b"\x00\xff executable bytes\r\n" * 100)
    target = (system, "amd64", name)
    archive = tmp_path / release.compress_binary(tmp_path, target)
    original = archive.read_bytes()
    assert release.archive_matches(archive, binary, system)
    release.compress_binary(tmp_path, target)
    assert archive.read_bytes() == original
    binary.write_bytes(b"changed executable")
    assert not release.archive_matches(archive, binary, system)
    release.compress_binary(tmp_path, target)
    assert release.archive_matches(archive, binary, system)
    assert archive.read_bytes() != original
    archive.write_bytes(b"corrupt")
    release.compress_binary(tmp_path, target)
    assert release.archive_matches(archive, binary, system)
