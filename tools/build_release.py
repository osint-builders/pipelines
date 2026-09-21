"""Build all five release binaries locally, without contacting GitHub."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from build_cli import ROOT, run, verify_bundle
from release import BINARY_TARGETS, prepare_assets


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--directory", type=Path, default=Path("dist/release"))
    parser.add_argument(
        "--archives-only",
        action="store_true",
        help="Package existing binaries without rebuilding",
    )
    args = parser.parse_args()
    manifest = verify_bundle(args.bundle)
    args.directory.mkdir(parents=True, exist_ok=True)
    if args.archives_only:
        recorded = json.loads((args.directory / "dataset-manifest.json").read_text())
        if recorded != manifest:
            parser.error("Existing release manifest differs; rebuild the binaries")
        prepare_assets(args.directory)
        return
    host = tuple(run(["go", "env", "GOHOSTOS", "GOHOSTARCH"], capture=True).split())
    for system, architecture, name in BINARY_TARGETS:
        command = [
            sys.executable,
            str(ROOT / "tools/build_cli.py"),
            "--bundle",
            str(args.bundle.resolve()),
            "--output",
            str((args.directory / name).resolve()),
        ]
        if (system, architecture) != host:
            command.append("--skip-run")
        subprocess.run(
            command,
            check=True,
            env={
                **os.environ,
                "GOOS": system,
                "GOARCH": architecture,
                "CGO_ENABLED": "0",
            },
        )
    (args.directory / "dataset-manifest.json").write_text(
        json.dumps(manifest, indent=2), encoding="utf-8", newline="\n"
    )
    prepare_assets(args.directory)
    print(
        f"Built {args.directory}. Cross-compiled targets still require runtime acceptance."
    )


if __name__ == "__main__":
    main()
