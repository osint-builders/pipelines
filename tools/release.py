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
                if archive.namelist() != ["pipelines.exe"]:
                    return False
                with archive.open("pipelines.exe") as zip_member:
                    return stream_digest(zip_member) == expected
        with tarfile.open(path, "r:xz") as archive:
            members = archive.getmembers()
            if (
                len(members) != 1
                or members[0].name != "pipelines"
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
            info = zipfile.ZipInfo("pipelines.exe", date_time=(1980, 1, 1, 0, 0, 0))
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
            tar_info = tarfile.TarInfo("pipelines")
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
    names.append("dataset-manifest.json")
    (directory / "SHA256SUMS").write_text(
        "".join(
            f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
            for name in names
        ),
        encoding="utf-8",
        newline="\n",
    )
    return [*names, "SHA256SUMS"]


def gh(*arguments: str) -> str:
    return subprocess.run(
        ["gh", *arguments], check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


def changed(current: dict, previous: dict | None) -> bool:
    return previous is None or current["content_sha256"] != previous["content_sha256"]


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


def gate(repo: str, tag: str, output: Path) -> dict:
    if not re.fullmatch(r"data-[0-9a-f]{64}", tag):
        raise ValueError("Input tag must be data- followed by the full content SHA-256")
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
    if tag != "data-" + manifest["content_sha256"]:
        raise ValueError("Input tag does not match content fingerprint")
    with tempfile.TemporaryDirectory() as temporary:
        previous = latest_manifest(repo, Path(temporary))
    result = {
        "changed": str(changed(manifest, previous)).lower(),
        "tag": "cli-" + manifest["dataset_id"],
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


def stage(repo: str, bundle: Path) -> None:
    manifest = verify_bundle(bundle)
    tag = "data-" + manifest["content_sha256"]
    with tempfile.TemporaryDirectory() as temporary:
        previous = latest_manifest(repo, Path(temporary))
    if not changed(manifest, previous):
        print("Content unchanged; no upload or release requested.")
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
        gh(
            "release",
            "create",
            tag,
            str(upload),
            "--repo",
            repo,
            "--prerelease",
            "--latest=false",
            "--title",
            f"Dataset input {manifest['content_sha256'][:16]}",
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
) -> None:
    manifest = json.loads((directory / "dataset-manifest.json").read_text())
    if tag != "cli-" + manifest["dataset_id"]:
        raise ValueError("Release tag does not match dataset")
    with tempfile.TemporaryDirectory() as temporary:
        if not changed(manifest, latest_manifest(repo, Path(temporary))):
            print("Content already published; skipping.")
            return
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
        target = target or os.environ.get("GITHUB_SHA")
        if not target:
            raise ValueError("Local publication requires --target COMMIT_SHA")
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
            f"pipelines CLI {manifest['dataset_id'][:16]}",
            "--notes",
            "",
            "--draft",
        )
    # GitHub's REST lookup by tag omits unpublished drafts. The CLI resolves both.
    uploaded = json.loads(
        gh("release", "view", tag, "--repo", repo, "--json", "isDraft,assets")
    )
    sizes = {asset["name"]: asset["size"] for asset in uploaded["assets"]}
    if not uploaded["isDraft"] or sizes != {
        name: (directory / name).stat().st_size for name in asset_names
    }:
        raise ValueError(
            "Draft release assets are incomplete; previous release is unchanged"
        )
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
    parser.add_argument("command", choices=["stage", "gate", "publish"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--directory", type=Path, default=Path("build/release"))
    parser.add_argument("--target", help="Commit SHA for a manually built release")
    args = parser.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", args.repo):
        parser.error("Invalid repository")
    if args.command == "stage":
        if args.bundle is None:
            parser.error("stage requires --bundle")
        stage(args.repo, args.bundle)
    elif args.command == "gate":
        print(json.dumps(gate(args.repo, args.tag or "", args.directory)))
    else:
        publish(
            args.repo,
            args.directory,
            args.tag or "",
            target=args.target,
        )


if __name__ == "__main__":
    main()
