import hashlib
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
import release
from measure_release import (
    CONTRACT,
    MODES,
    query_commands,
    reference_checks,
    resource_checks,
)
from quality_gates import REQUIRED_RELEASE_CHECKS


def manifest_for(extensions: dict | None = None) -> dict:
    content = hashlib.sha256(b"unchanged source text").hexdigest()
    recipe = hashlib.sha256(
        json.dumps(extensions or {}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "format_version": 4 if extensions else 2,
        "content_sha256": content,
        "recipe_sha256": recipe,
        "dataset_id": hashlib.sha256((content + recipe).encode()).hexdigest(),
        **(extensions or {}),
    }


@pytest.mark.parametrize("extension", ["image", "observations", "search", "research"])
def test_release_gate_includes_extension_changes_without_text_changes(
    extension: str,
) -> None:
    previous = manifest_for({extension: {"artifact_sha256": "a" * 64}})
    current = manifest_for({extension: {"artifact_sha256": "b" * 64}})
    assert previous["content_sha256"] == current["content_sha256"]
    assert release.changed(current, previous)
    assert release.input_tag(current) != release.input_tag(previous)


def test_release_gate_reuses_identical_dataset_and_reads_legacy_metadata() -> None:
    previous = manifest_for()
    assert not release.changed(dict(previous), previous)
    assert release.changed(previous, None)
    assert release.changed(previous, {"content_sha256": previous["content_sha256"]})
    assert release.changed(manifest_for({"image": {"views": 1}}), previous)


def test_release_input_identity_is_deterministic_for_equivalent_extensions() -> None:
    first = manifest_for({"image": {"views": 1}, "research": {"claims": 2}})
    second = manifest_for({"research": {"claims": 2}, "image": {"views": 1}})
    assert release.input_tag(first) == release.input_tag(second)
    assert not release.changed(first, second)


def test_legacy_input_tags_are_accepted_only_for_original_text_bundles() -> None:
    legacy = manifest_for()
    assert release.matches_input_tag(release.input_tag(legacy), legacy)
    assert release.matches_input_tag("data-" + legacy["content_sha256"], legacy)
    assert not release.matches_input_tag("data-" + "0" * 64, legacy)
    for extension in ("image", "observations", "search", "research"):
        current = manifest_for({extension: {"version": 1}})
        assert release.matches_input_tag(release.input_tag(current), current)
        assert not release.matches_input_tag(
            "data-" + current["content_sha256"], current
        )


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


def test_cli_identity_changes_with_code_or_dataset() -> None:
    manifest = manifest_for()
    assert release.cli_tag(manifest, "a" * 40) != release.cli_tag(manifest, "b" * 40)
    assert release.cli_tag(manifest, "a" * 40) != release.cli_tag(
        manifest_for({"image": {"views": 1}}), "a" * 40
    )
    for revision in (None, "", "main", "a" * 12, "z" * 40):
        with pytest.raises(ValueError, match="commit SHA"):
            release.cli_tag(manifest, revision)


@pytest.mark.parametrize("legacy", [True, False])
def test_release_identity_preserves_full_dataset_without_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, legacy: bool
) -> None:
    manifest = manifest_for()
    revision = "c" * 40
    tag = (
        f"cli-{manifest['dataset_id']}-{revision[:12]}"
        if legacy
        else release.cli_tag(manifest, revision)
    )
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a,
            0,
            json.dumps(
                {"tag_name": tag, "body": release.release_notes(manifest, revision)}
            ),
            "",
        ),
    )
    assert release.latest_manifest("owner/repo", tmp_path) == {
        "dataset_id": manifest["dataset_id"],
        "cli_revision": revision[:12],
    }


@pytest.mark.parametrize("fault", ["missing", "dataset", "revision", "duplicate"])
def test_short_release_tags_require_matching_full_identities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str
) -> None:
    manifest = manifest_for()
    body = release.release_notes(manifest, "c" * 40)
    if fault == "missing":
        body = ""
    elif fault == "dataset":
        body = body.replace(manifest["dataset_id"], "a" * 64)
    elif fault == "revision":
        body = body.replace("c" * 40, "d" * 40)
    else:
        body += body
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a,
            0,
            json.dumps({"tag_name": release.cli_tag(manifest, "c" * 40), "body": body}),
            "",
        ),
    )
    with pytest.raises(ValueError, match="Release notes"):
        release.latest_manifest("owner/repo", tmp_path)


@pytest.mark.parametrize("fault", [None, "revision", "dirty", "platform", "missing"])
def test_publication_checks_embedded_build_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fault: str | None
) -> None:
    revision = "c" * 40
    calls = []

    def metadata(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(args)
        system, arch, name = next(
            row for row in release.BINARY_TARGETS if row[2] == Path(args[-1]).name
        )
        settings = {
            "GOOS": system,
            "GOARCH": arch,
            "CGO_ENABLED": "0",
            "vcs": "git",
            "vcs.revision": revision,
            "vcs.modified": "false",
        }
        # Corrupt only the last platform: all five binaries must be checked.
        if name.endswith(".exe"):
            if fault == "revision":
                settings["vcs.revision"] = "d" * 40
            elif fault == "dirty":
                settings["vcs.modified"] = "true"
            elif fault == "platform":
                settings["GOARCH"] = "arm64"
            elif fault == "missing":
                settings.pop("vcs.revision")
        body = "binary: go1.27.0\n" + "".join(
            f"\tbuild\t{k}={v}\n" for k, v in settings.items()
        )
        return subprocess.CompletedProcess(args, 0, body, "")

    monkeypatch.setattr(release.subprocess, "run", metadata)
    if fault:
        with pytest.raises(ValueError, match="source or platform mismatch"):
            release.verify_binary_revision(tmp_path, revision)
    else:
        release.verify_binary_revision(tmp_path, revision)
    assert len(calls) == 5


@pytest.mark.parametrize("case", ["missing", "matching", "wrong", "denied"])
def test_existing_tag_must_resolve_to_the_validated_commit(
    monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    revision = "c" * 40
    response = {
        "missing": (1, "", "HTTP 404"),
        "matching": (0, json.dumps({"sha": revision}), ""),
        "wrong": (0, json.dumps({"sha": "d" * 40}), ""),
        "denied": (1, "", "HTTP 403 forbidden"),
    }[case]
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, *response),
    )
    if case == "wrong":
        with pytest.raises(ValueError, match="different source commit"):
            release.verify_tag_target("owner/repo", "cli-test", revision)
    elif case == "denied":
        with pytest.raises(RuntimeError, match="403"):
            release.verify_tag_target("owner/repo", "cli-test", revision)
    else:
        release.verify_tag_target("owner/repo", "cli-test", revision)


def test_latest_compact_release_needs_no_manifest_asset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = manifest_for()
    tag = release.cli_tag(manifest, "c" * 40)
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            a,
            0,
            json.dumps(
                {"tag_name": tag, "body": release.release_notes(manifest, "c" * 40)}
            ),
            "",
        ),
    )

    def forbidden(*args: str) -> str:
        raise AssertionError("compact release should not download a manifest")

    monkeypatch.setattr(release, "gh", forbidden)
    assert release.latest_manifest("owner/repo", tmp_path) == {
        "dataset_id": manifest["dataset_id"],
        "cli_revision": "c" * 12,
    }


@pytest.mark.parametrize(
    "previous_revision,expected",
    [(None, "true"), ("a" * 12, "true"), ("b" * 12, "false")],
)
def test_gate_allows_cli_updates_with_the_same_dataset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    previous_revision: str | None,
    expected: str,
) -> None:
    manifest = manifest_for()
    (tmp_path / "dataset.zip").write_bytes(b"verified dataset")
    monkeypatch.setattr(release, "verify_bundle", lambda path: manifest)
    monkeypatch.setattr(release, "gh", lambda *args: "")
    monkeypatch.setattr(
        release,
        "latest_manifest",
        lambda *args: {**manifest, "cli_revision": previous_revision},
    )
    result = release.gate(
        "owner/repo", release.input_tag(manifest), tmp_path, target="b" * 40
    )
    assert result["changed"] == expected
    assert result["tag"] == release.cli_tag(manifest, "b" * 40)


def test_publication_rejects_the_wrong_revision_before_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = manifest_for()
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest))

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("invalid release identity reached validation")

    monkeypatch.setattr(release, "validate_release", forbidden)
    with pytest.raises(ValueError, match="dataset and CLI revision"):
        release.publish(
            "owner/repo",
            tmp_path,
            release.cli_tag(manifest, "a" * 40),
            target="b" * 40,
            bundle=tmp_path / "dataset.zip",
            validation=tmp_path,
        )


def test_old_executables_cannot_be_published_under_a_new_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = manifest_for()
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest))
    monkeypatch.setattr(release, "validate_release", lambda *a, **k: None)

    def run(args: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        assert args[:3] == ["go", "version", "-m"], (
            "network access before source validation"
        )
        return subprocess.CompletedProcess(
            args, 0, "\tbuild\tvcs.revision=" + "a" * 40 + "\n", ""
        )

    monkeypatch.setattr(release.subprocess, "run", run)
    with pytest.raises(ValueError, match="source or platform mismatch"):
        release.publish(
            "owner/repo",
            tmp_path,
            release.cli_tag(manifest, "b" * 40),
            target="b" * 40,
            bundle=tmp_path / "dataset.zip",
            validation=tmp_path,
        )


def test_unchanged_content_cannot_upload_an_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = manifest_for()
    monkeypatch.setattr(release, "verify_bundle", lambda path: manifest)
    monkeypatch.setattr(release, "latest_manifest", lambda repo, path: manifest)

    def forbidden(*args: str) -> str:
        raise AssertionError("upload was attempted")

    monkeypatch.setattr(release, "gh", forbidden)
    release.stage("owner/repo", tmp_path / "unused.zip")


@pytest.mark.parametrize("complete", [True, False])
@pytest.mark.parametrize("local", [True, False])
@pytest.mark.parametrize("corrupted", [False, True])
@pytest.mark.parametrize("draft_identity", ["matching", "wrong_tag", "wrong_commit"])
def test_publication_waits_for_complete_draft_assets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    complete: bool,
    local: bool,
    corrupted: bool,
    draft_identity: str,
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
            return json.dumps({"databaseId": 1})
        if args[0] == "api":
            uploaded = [
                *(
                    release.archive_name(system, name)
                    for system, _, name in release.BINARY_TARGETS
                ),
                "SHA256SUMS",
            ]
            if not complete:
                uploaded.pop()
            return json.dumps(
                {
                    "draft": True,
                    "tag_name": release.cli_tag(manifest, ("d" if local else "c") * 40)
                    if draft_identity != "wrong_tag"
                    else "cli-other",
                    "target_commitish": ("d" if local else "c") * 40
                    if draft_identity != "wrong_commit"
                    else "a" * 40,
                    "assets": [
                        {
                            "name": name,
                            "size": (tmp_path / name).stat().st_size,
                            "digest": "sha256:"
                            + (
                                "0" * 64
                                if corrupted
                                else release.asset_sha256(tmp_path / name)
                            ),
                        }
                        for name in uploaded
                    ],
                }
            )
        return ""

    if local:
        monkeypatch.delenv("GITHUB_SHA", raising=False)
    else:
        monkeypatch.setenv("GITHUB_SHA", "c" * 40)
    monkeypatch.setattr(release, "latest_manifest", lambda repo, path: None)
    monkeypatch.setattr(release, "validate_release", lambda *args, **kwargs: None)
    monkeypatch.setattr(release, "verify_binary_revision", lambda *args: None)
    monkeypatch.setattr(release, "gh", fake_gh)
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 1, "", "HTTP 404"),
    )
    if complete and not corrupted and draft_identity == "matching":
        release.publish(
            "owner/repo",
            tmp_path,
            release.cli_tag(manifest, ("d" if local else "c") * 40),
            target="d" * 40 if local else None,
            bundle=tmp_path / "dataset.zip",
            validation=tmp_path / "validation",
        )
        assert calls[-1][:2] == ("release", "edit")
        assert "--draft=false" in calls[-1]
        assert calls[-1][calls[-1].index("--notes") + 1] == release.release_notes(
            manifest, ("d" if local else "c") * 40
        )
    else:
        with pytest.raises(
            ValueError,
            match="source commit and tag"
            if draft_identity != "matching"
            else "incomplete"
            if not complete
            else "checksum mismatch",
        ):
            release.publish(
                "owner/repo",
                tmp_path,
                release.cli_tag(manifest, ("d" if local else "c") * 40),
                target="d" * 40 if local else None,
                bundle=tmp_path / "dataset.zip",
                validation=tmp_path / "validation",
            )
        assert not any(call[:2] == ("release", "edit") for call in calls)
    assert calls[0][:2] == ("release", "create")
    assert "--draft" in calls[0] and "--latest" not in calls[0]
    assert calls[0][calls[0].index("--target") + 1] == ("d" if local else "c") * 40
    assert calls[0][calls[0].index("--notes") + 1] == release.release_notes(
        manifest, ("d" if local else "c") * 40
    )
    assert not list(tmp_path.glob("*.md"))


def test_publication_requires_validation_before_any_network_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    manifest = manifest_for({"image": {"views": 1}})
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest))

    def forbidden(*args: object) -> None:
        raise AssertionError("network call before validation")

    monkeypatch.setattr(release, "gh", forbidden)
    monkeypatch.setattr(release, "latest_manifest", forbidden)
    with pytest.raises(ValueError, match="--validation"):
        release.publish("owner/repo", tmp_path, "cli-" + manifest["dataset_id"])


def test_media_only_changes_stage_a_distinct_dataset_tag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    previous = manifest_for({"image": {"views": 1}})
    current = manifest_for({"image": {"views": 2}})
    bundle = tmp_path / "dataset.zip"
    bundle.write_bytes(b"verified test bundle")
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(release, "verify_bundle", lambda path: current)
    monkeypatch.setattr(release, "latest_manifest", lambda *args: previous)
    monkeypatch.setattr(release, "gh", lambda *args: calls.append(args))
    monkeypatch.setattr(
        release.subprocess,
        "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", "HTTP 404"),
    )
    release.stage("owner/repo", bundle)
    assert calls[0][:3] == ("release", "create", release.input_tag(current))
    assert release.input_tag(previous) not in calls[0]


def test_portable_quality_evidence_preserves_hashes_and_is_deterministic(
    tmp_path: Path,
) -> None:
    fixture = tmp_path / "fixture.json"
    fixture.write_bytes(b'{"cases": []}')
    report = {
        "evidence": {
            "fixture": {
                "path": str(fixture),
                "sha256": hashlib.sha256(fixture.read_bytes()).hexdigest(),
            },
            "binary": {"path": "not-copied"},
            "bundle": {"path": "not-copied"},
        }
    }
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    release.pack_quality_evidence(report, first)
    release.pack_quality_evidence(report, second)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["fixture"]
        assert archive.read("fixture") == fixture.read_bytes()
    fixture.write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        release.pack_quality_evidence(report, second)


@pytest.fixture
def release_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[Path, Path, Path]:
    validation = tmp_path / "validation"
    validation.mkdir()
    bundle = tmp_path / "dataset.zip"
    bundle.write_bytes(b"verified bundle fixture")
    manifest = manifest_for({"image": {"preview_bytes": 100}})
    (tmp_path / "dataset-manifest.json").write_text(json.dumps(manifest))
    contract = json.loads(CONTRACT.read_bytes())
    query = {"query": "radar", "source": "radartutorial", "mode": "hybrid"}
    monkeypatch.setattr(release, "verify_bundle", lambda path: manifest)
    monkeypatch.setattr("quality_gates.validate_report", lambda *args: None)
    common = {
        "schema_version": 1,
        "dataset_id": manifest["dataset_id"],
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
        "contract_sha256": hashlib.sha256(CONTRACT.read_bytes()).hexdigest(),
    }
    for system, architecture, name in release.BINARY_TARGETS:
        binary = tmp_path / name
        binary.write_bytes(name.encode())
        packed = tmp_path / release.compress_binary(
            tmp_path, (system, architecture, name)
        )
        target = f"{system}-{architecture}"
        report = {
            **common,
            "target": target,
            "native_target": target,
            "binary_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "binary": {"bytes": binary.stat().st_size},
            "archive": {
                "bytes": packed.stat().st_size,
                "sha256": hashlib.sha256(packed.read_bytes()).hexdigest(),
            },
            "preview_bytes": 100,
            "content_sha256": "source",
            "text_query": query,
            "hardware": {"cpu": "reference", "os": "Windows"},
            "acceptance": {"dataset_id": manifest["dataset_id"]},
            "verification": {
                "dataset_id": manifest["dataset_id"],
                "ok": True,
                "probes": 4,
                "image_probes": 3,
                "observation_probes": 3,
            },
            "modes": {
                mode: {
                    "samples": [
                        {"seconds": 1, "peak_rss_bytes": 1000000} for _ in range(21)
                    ],
                    "stable": True,
                    "args": query_commands(query, "<query image>")[mode],
                }
                for mode in MODES
            },
            "resource_gates_passed": True,
        }
        report["checks"] = [
            {"name": key, "passed": value}
            for key, value in resource_checks(report, contract).items()
        ]
        (validation / f"{target}.json").write_text(json.dumps(report))
        if system == "windows":
            quality = {
                **common,
                "binary_sha256": report["binary_sha256"],
                "checks": [
                    {"name": name, "passed": True} for name in REQUIRED_RELEASE_CHECKS
                ],
                "release_quality_established": True,
            }
            (validation / "quality.json").write_text(json.dumps(quality))
            report["baseline"] = {
                "hardware": report["hardware"],
                "dataset": {
                    "dataset_id": contract["baseline"]["dataset_id"],
                    "content_sha256": "source",
                },
                "latency": {
                    "probe_query": report["text_query"],
                    "repeat_seconds": {"p95": 1},
                },
                "peak_rss_bytes": 1000000,
            }
            report["checks"].extend(
                {"name": key, "passed": value}
                for key, value in reference_checks(
                    report, report["baseline"], contract
                ).items()
            )
            (validation / "reference.json").write_text(json.dumps(report))
    return tmp_path, bundle, validation


def test_release_validation_binds_all_native_binaries_and_recomputes_resources(
    release_candidate: tuple[Path, Path, Path],
) -> None:
    directory, bundle, validation = release_candidate
    release.validate_release(directory, bundle, validation)
    report_path = validation / "linux-arm64.json"
    report = json.loads(report_path.read_bytes())
    for sample in report["modes"]["text"]["samples"]:
        sample["seconds"] = 99
    report_path.write_text(json.dumps(report))
    with pytest.raises(ValueError, match="resource gates failed"):
        release.validate_release(directory, bundle, validation)


@pytest.mark.parametrize(
    "fault", ["quality_check", "dataset", "missing_target", "binary", "reference"]
)
def test_release_validation_fails_closed(
    release_candidate: tuple[Path, Path, Path], fault: str
) -> None:
    directory, bundle, validation = release_candidate
    if fault == "missing_target":
        (validation / "darwin-arm64.json").unlink()
    elif fault == "binary":
        (directory / "pipelines-linux-amd64").write_bytes(b"different executable")
    else:
        report_path = validation / (
            "reference.json" if fault == "reference" else "quality.json"
        )
        report = json.loads(report_path.read_bytes())
        if fault == "quality_check":
            report["checks"].pop()
        elif fault == "dataset":
            report["dataset_id"] = "other"
        else:
            report["hardware"]["cpu"] = "other machine"
        report_path.write_text(json.dumps(report))
    with pytest.raises((ValueError, FileNotFoundError)):
        release.validate_release(directory, bundle, validation)


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
    if system == "windows":
        with zipfile.ZipFile(archive) as packed:
            assert packed.namelist() == ["pipeline.exe"]
    else:
        import tarfile

        with tarfile.open(archive) as packed_tar:
            assert packed_tar.getnames() == ["pipeline"]
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
