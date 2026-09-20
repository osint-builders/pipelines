"""Explicit GitHub publication helpers. Importing this module has no side effects."""

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from build_cli import verify_bundle


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
            "Verified input for the explicit Release CLI workflow.",
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


def publish(repo: str, directory: Path, tag: str) -> None:
    manifest = json.loads((directory / "dataset-manifest.json").read_text())
    if tag != "cli-" + manifest["dataset_id"]:
        raise ValueError("Release tag does not match dataset")
    with tempfile.TemporaryDirectory() as temporary:
        if not changed(manifest, latest_manifest(repo, Path(temporary))):
            print("Content already published; skipping.")
            return
    expected = [
        "pipelines-linux-amd64",
        "pipelines-linux-arm64",
        "pipelines-darwin-amd64",
        "pipelines-darwin-arm64",
        "pipelines-windows-amd64.exe",
    ]
    for name in expected:
        if not (directory / name).is_file():
            raise ValueError(f"Missing release binary: {name}")
    checksums = "".join(
        f"{hashlib.sha256((directory / name).read_bytes()).hexdigest()}  {name}\n"
        for name in [*expected, "dataset-manifest.json"]
    )
    (directory / "SHA256SUMS").write_text(checksums, encoding="utf-8", newline="\n")
    notes = directory / "release-notes.md"
    notes.write_text(
        f"Offline search over {manifest['entities']:,} source entities.\n\n"
        f"Dataset: `{manifest['dataset_id']}`\n\n"
        "Download the executable for your platform. No installation, API key, or model download is required.\n\n"
        'Run `pipelines search "your query"`, then `pipelines get SOURCE:ID`.\n'
        "Run `pipelines --help` for filters, pure vector search, and full HTML export.\n",
        encoding="utf-8",
    )
    asset_names = [*expected, "dataset-manifest.json", "SHA256SUMS"]
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
        gh(
            "release",
            "create",
            tag,
            *assets,
            "--repo",
            repo,
            "--target",
            os.environ["GITHUB_SHA"],
            "--title",
            f"Reference data {manifest['content_sha256'][:16]}",
            "--notes-file",
            str(notes),
            "--draft",
        )
    uploaded = json.loads(gh("api", f"repos/{repo}/releases/tags/{tag}"))
    sizes = {asset["name"]: asset["size"] for asset in uploaded["assets"]}
    if not uploaded["draft"] or sizes != {
        name: (directory / name).stat().st_size for name in asset_names
    }:
        raise ValueError(
            "Draft release assets are incomplete; previous release is unchanged"
        )
    gh("release", "edit", tag, "--repo", repo, "--draft=false", "--latest")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["stage", "gate", "publish"])
    parser.add_argument("--repo", required=True)
    parser.add_argument("--bundle", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--directory", type=Path, default=Path("build/release"))
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
        publish(args.repo, args.directory, args.tag or "")


if __name__ == "__main__":
    main()
