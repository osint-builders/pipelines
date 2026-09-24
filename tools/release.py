"""Explicit GitHub publication helpers. Importing this module has no side effects."""

import argparse
import hashlib
import json
import lzma
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import IO

from build_cli import verify_bundle

BINARY_TARGETS = (
    ("linux", "amd64", "pipelines-linux-amd64"),
    ("linux", "arm64", "pipelines-linux-arm64"),
    ("darwin", "amd64", "pipelines-darwin-amd64"),
    ("darwin", "arm64", "pipelines-darwin-arm64"),
    ("windows", "amd64", "pipelines-windows-amd64.exe"),
)


def archive_name(system: str, binary_name: str) -> str:
    return binary_name.removesuffix(".exe") + (
        ".zip" if system == "windows" else ".tar.xz"
    )


def stream_digest(member: IO[bytes]) -> bytes:
    digest = hashlib.sha256()
    while block := member.read(1024 * 1024):
        digest.update(block)
    return digest.digest()


def archive_matches(path: Path, binary: Path, system: str) -> bool:
    """Verify the single executable in an archive without extracting to disk."""
    expected = hashlib.sha256(binary.read_bytes()).digest()
    try:
        if system == "windows":
            with zipfile.ZipFile(path) as archive:
                if archive.namelist() != ["pipeline.exe"]:
                    return False
                with archive.open("pipeline.exe") as zip_member:
                    return stream_digest(zip_member) == expected
        with tarfile.open(path, "r:xz") as archive:
            members = archive.getmembers()
            if (
                len(members) != 1
                or members[0].name != "pipeline"
                or not members[0].isfile()
                or members[0].mode != 0o755
            ):
                return False
            tar_member = archive.extractfile(members[0])
            assert tar_member is not None
            with tar_member:
                return stream_digest(tar_member) == expected
    except (
        OSError,
        ValueError,
        EOFError,
        tarfile.TarError,
        zipfile.BadZipFile,
        lzma.LZMAError,
    ):
        return False


def compress_binary(directory: Path, target: tuple[str, str, str]) -> str:
    system, _, name = target
    binary = directory / name
    if not binary.is_file():
        raise ValueError(f"Missing release binary: {name}")
    output = directory / archive_name(system, name)
    if output.exists() and archive_matches(output, binary, system):
        return output.name
    temporary = output.with_suffix(output.suffix + ".tmp")
    if system == "windows":
        with zipfile.ZipFile(temporary, "w") as archive:
            info = zipfile.ZipInfo("pipeline.exe", date_time=(1980, 1, 1, 0, 0, 0))
            info.external_attr = 0o100755 << 16
            archive.writestr(
                info,
                binary.read_bytes(),
                compress_type=zipfile.ZIP_DEFLATED,
                compresslevel=9,
            )
    else:
        with (
            lzma.open(temporary, "wb", preset=9 | lzma.PRESET_EXTREME) as compressed,
            tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.USTAR_FORMAT
            ) as archive,
        ):
            tar_info = tarfile.TarInfo("pipeline")
            tar_info.size = binary.stat().st_size
            tar_info.mode = 0o755
            with binary.open("rb") as member:
                archive.addfile(tar_info, member)
    if not archive_matches(temporary, binary, system):
        raise ValueError(f"Compressed executable failed verification: {name}")
    temporary.replace(output)
    return output.name


def prepare_assets(directory: Path) -> list[str]:
    # Two compressors bound memory allocation with the maximum LZMA dictionary.
    with ThreadPoolExecutor(max_workers=2) as pool:
        names = list(
            pool.map(lambda target: compress_binary(directory, target), BINARY_TARGETS)
        )
    # Only executables are public downloads. Other build inputs and validation
    # artifacts stay out even when they share this directory.
    (directory / "SHA256SUMS").write_text(
        "".join(f"{asset_sha256(directory / name)}  {name}\n" for name in names),
        encoding="utf-8",
        newline="\n",
    )
    return [*names, "SHA256SUMS"]


def asset_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def pack_quality_evidence(report: dict, output: Path) -> None:
    from quality_gates import checked_artifact

    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, reference in sorted(report["evidence"].items()):
            if not re.fullmatch(r"[a-z_]+", name):
                raise ValueError("Invalid quality evidence name")
            if name not in {"binary", "bundle"}:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                archive.writestr(
                    info,
                    checked_artifact(reference).read_bytes(),
                    compress_type=zipfile.ZIP_DEFLATED,
                    compresslevel=9,
                )


def validate_release(
    directory: Path, bundle: Path, validation: Path, *, profile: str = "identification"
) -> None:
    from measure_release import (
        CONTRACT,
        RESEARCH_CONTRACT,
        reference_checks,
        resource_checks,
    )
    from quality_gates import REQUIRED_RELEASE_CHECKS, validate_report

    if profile not in {"identification", "research"}:
        raise ValueError("Unknown release validation profile")
    contract_path = RESEARCH_CONTRACT if profile == "research" else CONTRACT
    manifest = verify_bundle(bundle)
    if json.loads((directory / "dataset-manifest.json").read_bytes()) != manifest:
        raise ValueError("Release manifest does not match the verified bundle")
    bundle_hash = hashlib.sha256(bundle.read_bytes()).hexdigest()
    contract = json.loads(contract_path.read_bytes())
    contract_hash = hashlib.sha256(contract_path.read_bytes()).hexdigest()

    def read_report(path: Path) -> dict:
        report = json.loads(path.read_bytes())
        if (
            report.get("schema_version") != 1
            or report.get("dataset_id") != manifest["dataset_id"]
            or report.get("bundle_sha256") != bundle_hash
        ):
            raise ValueError(f"Release validation identity mismatch: {path.name}")
        return report

    def require_checks(report: dict, required: tuple[str, ...]) -> None:
        rows = report.get("checks", [])
        names = [row.get("name") for row in rows]
        if (
            len(set(names)) != len(names)
            or not set(required).issubset(names)
            or any(row.get("passed") is not True for row in rows)
        ):
            raise ValueError("Release validation has missing or failed checks")

    quality = read_report(validation / "quality.json")
    if profile == "research":
        from research_quality import (
            PROFILE,
        )
        from research_quality import (
            validate_report as validate_research_report,
        )

        if (
            quality.get("profile") != PROFILE
            or quality.get("research_release_ready") is not True
        ):
            raise ValueError("Research release quality is not established")
    else:
        require_checks(quality, REQUIRED_RELEASE_CHECKS)
        if quality.get("release_quality_established") is not True:
            raise ValueError("Release quality is not established")
    if quality.get("contract_sha256") != contract_hash:
        raise ValueError("Release quality uses a different acceptance contract")
    binary_hashes = {}
    for _, _, name in BINARY_TARGETS:
        binary = directory / name
        binary_hashes[hashlib.sha256(binary.read_bytes()).hexdigest()] = binary
    if quality.get("binary_sha256") not in binary_hashes:
        raise ValueError("Quality report does not describe a release executable")
    replacements = {"binary": binary_hashes[quality["binary_sha256"]], "bundle": bundle}
    with tempfile.TemporaryDirectory() as temporary:
        packed = validation / "quality-evidence.zip"
        if packed.is_file():
            with zipfile.ZipFile(packed) as evidence_archive:
                expected = set(quality["evidence"]) - {"binary", "bundle"}
                if set(evidence_archive.namelist()) != expected or len(
                    evidence_archive.namelist()
                ) != len(expected):
                    raise ValueError(
                        "Quality evidence archive has missing or duplicate artifacts"
                    )
                for name in expected:
                    if not re.fullmatch(r"[a-z_]+", name):
                        raise ValueError("Invalid quality evidence name")
                    path = Path(temporary) / name
                    path.write_bytes(evidence_archive.read(name))
                    replacements[name] = path
        if profile == "research":
            validate_research_report(quality, replacements)
        else:
            validate_report(quality, replacements)
    for system, architecture, name in BINARY_TARGETS:
        target = f"{system}-{architecture}"
        report = read_report(validation / f"{target}.json")
        binary = directory / name
        binary_hash = hashlib.sha256(binary.read_bytes()).hexdigest()
        archive = directory / archive_name(system, name)
        if (
            report.get("target") != target
            or report.get("binary_sha256") != binary_hash
            or report.get("binary", {}).get("bytes") != binary.stat().st_size
            or report.get("contract_sha256") != contract_hash
            or report.get("resource_gates_passed") is not True
            or report.get("archive", {}).get("sha256")
            != hashlib.sha256(archive.read_bytes()).hexdigest()
            or report["archive"].get("bytes") != archive.stat().st_size
            or report.get("preview_bytes")
            != manifest.get("image", {}).get("preview_bytes")
            or not archive_matches(archive, binary, system)
        ):
            raise ValueError(f"Native release validation mismatch: {target}")
        computed = resource_checks(report, contract)
        require_checks(report, tuple(computed))
        if not all(computed.values()):
            raise ValueError(f"Native resource gates failed: {target}")
        if profile == "research":
            from quality_gates import checked_artifact
            from research_quality import PROFILE, validate_text

            if report.get("profile") != PROFILE:
                raise ValueError("Native report uses a different research profile")
            # Packed evidence lives only inside the validation scope above.
            if (validation / "quality-evidence.zip").is_file():
                with zipfile.ZipFile(
                    validation / "quality-evidence.zip"
                ) as packed_evidence:
                    baseline = json.loads(packed_evidence.read("baseline"))
            else:
                baseline = json.loads(
                    checked_artifact(quality["evidence"]["baseline"]).read_bytes()
                )
            validate_text(
                json.loads((validation / f"{target}-text.json").read_bytes()),
                baseline,
                manifest["dataset_id"],
            )
    if profile == "research":
        return
    reference = read_report(validation / "reference.json")
    if (
        reference.get("binary_sha256")
        != hashlib.sha256(
            (directory / "pipelines-windows-amd64.exe").read_bytes()
        ).hexdigest()
        or reference.get("contract_sha256") != contract_hash
    ):
        raise ValueError("Reference report does not describe a release executable")
    computed = {
        **resource_checks(reference, contract),
        **reference_checks(reference, reference.get("baseline", {}), contract),
    }
    require_checks(reference, tuple(computed))
    if not all(computed.values()):
        raise ValueError("Reference resource regression gates failed")


def gh(*arguments: str) -> str:
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def changed(current: dict, previous: dict | None) -> bool:
    return previous is None or current["dataset_id"] != previous.get("dataset_id")


def input_tag(manifest: dict) -> str:
    return "data-" + manifest["dataset_id"]


def cli_tag(manifest: dict, revision: str | None) -> str:
    if not re.fullmatch(r"[0-9a-f]{40}", revision or ""):
        raise ValueError(
            "CLI release requires a full commit SHA (--target or GITHUB_SHA)"
        )
    if not re.fullmatch(r"[0-9a-f]{64}", manifest["dataset_id"]):
        raise ValueError("Invalid dataset identity")
    assert revision is not None
    return f"cli-{manifest['dataset_id']}-{revision[:12]}"


def matches_input_tag(tag: str, manifest: dict) -> bool:
    if tag == input_tag(manifest):
        return True
    # Preserve staged text inputs created before dataset-based release identities.
    legacy = manifest["format_version"] == 2 and not any(
        key in manifest for key in ("image", "observations", "search", "research")
    )
    return legacy and tag == "data-" + manifest["content_sha256"]


def latest_manifest(repo: str, directory: Path) -> dict | None:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/releases/latest"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if result.returncode:
        if "HTTP 404" in result.stderr:
            return None
        raise RuntimeError(result.stderr)
    release = json.loads(result.stdout)
    identity = re.fullmatch(r"cli-([0-9a-f]{64})-([0-9a-f]{12})", release["tag_name"])
    if identity:
        # The dataset manifest is embedded in each executable. New releases
        # expose their dataset and CLI identities without a separate download.
        return {"dataset_id": identity[1], "cli_revision": identity[2]}
    gh(
        "release",
        "download",
        release["tag_name"],
        "--repo",
        repo,
        "--pattern",
        "dataset-manifest.json",
        "--dir",
        str(directory),
    )
    return json.loads((directory / "dataset-manifest.json").read_text())


def gate(repo: str, tag: str, output: Path, *, target: str | None = None) -> dict:
    if not re.fullmatch(r"data-[0-9a-f]{64}", tag):
        raise ValueError("Input tag must be data- followed by the full dataset ID")
    output.mkdir(parents=True, exist_ok=True)
    gh(
        "release",
        "download",
        tag,
        "--repo",
        repo,
        "--pattern",
        "dataset.zip",
        "--dir",
        str(output),
        "--clobber",
    )
    bundle = output / "dataset.zip"
    manifest = verify_bundle(bundle)
    if not matches_input_tag(tag, manifest):
        raise ValueError("Input tag does not match dataset identity")
    revision = target or os.environ.get("GITHUB_SHA")
    release_tag = cli_tag(manifest, revision)
    assert revision is not None
    with tempfile.TemporaryDirectory() as temporary:
        previous = latest_manifest(repo, Path(temporary))
    result = {
        "changed": str(
            changed(manifest, previous)
            or (previous or {}).get("cli_revision") != revision[:12]
        ).lower(),
        "tag": release_tag,
        "bundle_sha256": hashlib.sha256(bundle.read_bytes()).hexdigest(),
    }
    (output / "dataset-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8"
    )
    if "GITHUB_OUTPUT" in os.environ:
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as handle:
            for key, value in result.items():
                handle.write(f"{key}={value}\n")
    return result


def stage(
    repo: str,
    bundle: Path,
    validation: Path | None = None,
    *,
    profile: str = "identification",
) -> None:
    manifest = verify_bundle(bundle)
    tag = input_tag(manifest)
    with tempfile.TemporaryDirectory() as temporary:
        previous = latest_manifest(repo, Path(temporary))
    if not changed(manifest, previous):
        print("Dataset unchanged; no upload or release requested.")
        return
    existing = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo], capture_output=True
    )
    if existing.returncode == 0:
        raise ValueError(
            f"Input {tag} already exists; dispatch its release workflow or use new data"
        )
    # Staged data is a prerelease and never changes the default CLI download.
    with tempfile.TemporaryDirectory() as temporary:
        upload = Path(temporary) / "dataset.zip"
        shutil.copyfile(bundle, upload)
        assets = [str(upload)]
        if validation is not None:
            for name in (
                ("quality.json",)
                if profile == "research"
                else ("quality.json", "reference.json")
            ):
                report = validation / name
                identity = json.loads(report.read_bytes())
                if identity.get("dataset_id") != manifest["dataset_id"]:
                    raise ValueError(f"Staged validation dataset mismatch: {name}")
                copied = Path(temporary) / name
                shutil.copyfile(report, copied)
                assets.append(str(copied))
            evidence = Path(temporary) / "quality-evidence.zip"
            pack_quality_evidence(
                json.loads((validation / "quality.json").read_bytes()), evidence
            )
            assets.append(str(evidence))
        gh(
            "release",
            "create",
            tag,
            *assets,
            "--repo",
            repo,
            "--prerelease",
            "--latest=false",
            "--title",
            f"Dataset input {manifest['dataset_id'][:16]}",
            "--notes",
            "",
        )
    print(
        json.dumps(
            {
                "input_tag": tag,
                "next": f"gh workflow run release-cli.yml --repo {repo} -f input_tag={tag}",
            },
            indent=2,
        )
    )


def publish(
    repo: str,
    directory: Path,
    tag: str,
    *,
    target: str | None = None,
    bundle: Path | None = None,
    validation: Path | None = None,
    profile: str = "identification",
) -> None:
    manifest = json.loads((directory / "dataset-manifest.json").read_text())
    if bundle is None or validation is None:
        raise ValueError("Publication requires --bundle and --validation reports")
    target = target or os.environ.get("GITHUB_SHA")
    if tag != cli_tag(manifest, target):
        raise ValueError("Release tag does not match dataset and CLI revision")
    validate_release(directory, bundle, validation, profile=profile)
    asset_names = prepare_assets(directory)
    assets = [str(directory / name) for name in asset_names]
    existing = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repo, "--json", "isDraft"],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if existing.returncode == 0:
        if not json.loads(existing.stdout)["isDraft"]:
            raise ValueError(
                f"Immutable release {tag} already exists; its binaries will not be overwritten"
            )
        gh("release", "upload", tag, *assets, "--repo", repo, "--clobber")
    else:
        assert target is not None
        gh(
            "release",
            "create",
            tag,
            *assets,
            "--repo",
            repo,
            "--target",
            target,
            "--title",
            f"pipeline CLI {target[:12]}",
            "--notes",
            "",
            "--draft",
        )
    # GitHub's REST lookup by tag omits unpublished drafts. The CLI resolves both.
    release_id = json.loads(
        gh("release", "view", tag, "--repo", repo, "--json", "databaseId")
    )["databaseId"]
    uploaded = json.loads(gh("api", f"repos/{repo}/releases/{release_id}"))
    sizes = {asset["name"]: asset["size"] for asset in uploaded["assets"]}
    if not uploaded["draft"] or sizes != {
        name: (directory / name).stat().st_size for name in asset_names
    }:
        raise ValueError(
            "Draft release assets are incomplete; previous release is unchanged"
        )
    if any(
        asset.get("digest") != "sha256:" + asset_sha256(directory / asset["name"])
        for asset in uploaded["assets"]
    ):
        raise ValueError("Uploaded release checksum mismatch; draft is not published")
    gh(
        "release",
        "edit",
        tag,
        "--repo",
        repo,
        "--draft=false",
        "--latest",
        "--notes",
        "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "gate", "validate", "publish"])
    parser.add_argument("--repo")
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--directory", type=Path, default=Path("build/release"))
    parser.add_argument("--target", help="Commit SHA for a manually built release")
    parser.add_argument(
        "--profile", choices=["identification", "research"], default="identification"
    )
    parser.add_argument(
        "--validation",
        type=Path,
        help="Directory of dataset-bound quality and native resource reports",
    )
    args = parser.parse_args()
    if args.command != "validate" and not re.fullmatch(
        r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo or ""
    ):
        parser.error("Invalid repository")
    if args.command == "stage":
        if args.bundle is None:
            parser.error("stage requires --bundle")
        stage(args.repo, args.bundle, args.validation, profile=args.profile)
    elif args.command == "gate":
        print(
            json.dumps(
                gate(args.repo, args.tag or "", args.directory, target=args.target)
            )
        )
    elif args.command == "validate":
        if args.bundle is None or args.validation is None:
            parser.error("validate requires --bundle and --validation")
        validate_release(
            args.directory, args.bundle, args.validation, profile=args.profile
        )
        print(json.dumps({"publication_ready": True}))
    else:
        publish(
            args.repo,
            args.directory,
            args.tag or "",
            target=args.target,
            bundle=args.bundle,
            validation=args.validation,
            profile=args.profile,
        )


if __name__ == "__main__":
    main()
