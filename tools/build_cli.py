"""Build one portable binary from a verified dataset and the current Go source."""

import argparse
import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(arguments: list[str], *, capture: bool = False) -> str:
    result = subprocess.run(
        arguments,
        cwd=ROOT / "cli",
        env={**os.environ, "CGO_ENABLED": "0"},
        check=True,
        text=True,
        encoding="utf-8",
        capture_output=capture,
    )
    return result.stdout if capture else ""


def verify_bundle(bundle: Path) -> dict:
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        lock = json.loads((ROOT / "src/pipelines/model.lock.json").read_text())
        if manifest["format_version"] not in {2, 3, 4} or manifest["model"] != lock:
            raise ValueError("Bundle format/model does not match this CLI")
        if (manifest["format_version"] >= 3) != ("image" in manifest):
            raise ValueError("Bundle format does not match its image extension")
        if (manifest["format_version"] == 4) != ("observations" in manifest):
            raise ValueError("Bundle format does not match its observation extension")
        expected = hashlib.sha256(
            (manifest["content_sha256"] + manifest["recipe_sha256"]).encode()
        ).hexdigest()
        if manifest["dataset_id"] != expected:
            raise ValueError("Invalid dataset identity")
        for name, digest in manifest["files"].items():
            if hashlib.sha256(archive.read(name)).hexdigest() != digest:
                raise ValueError(f"Bundle checksum mismatch: {name}")
        for name, spec in lock["files"].items():
            if manifest["files"][f"model/{name}"] != spec["sha256"]:
                raise ValueError(f"Wrong model asset: {name}")
        index = json.loads(archive.read("index.json"))
        entities = [
            json.loads(
                archive.read("entities/" + item["id"].replace(":", "/") + ".json")
            )
            for item in index
        ]
        if len(entities) != manifest["entities"] or not entities:
            raise ValueError("Invalid entity count")

        def stable_content(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: stable_content(item)
                    for key, item in value.items()
                    if key not in {"retrieved_at", "html_sha256", "source_response"}
                }
            if isinstance(value, list):
                return [stable_content(item) for item in value]
            return value

        stable = stable_content(entities)
        digest = hashlib.sha256(
            json.dumps(
                stable, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode()
        ).hexdigest()
        if digest != manifest["content_sha256"]:
            raise ValueError("Content fingerprint does not match entities")
        evidence_pages = set()
        for entity in entities:
            for page in entity["evidence"]:
                name = entity["source"] + "/" + page["id"]
                raw = archive.read("html/" + name + ".html")
                if hashlib.sha256(raw).hexdigest() != page["html_sha256"]:
                    raise ValueError("Original HTML does not match entity provenance")
                response = page.get("source_response")
                if page.get("html_origin") == "record-rendered":
                    record_id = page.get("record_id", "")
                    mime = (
                        (response or {})
                        .get("content_type", "")
                        .split(";", 1)[0]
                        .strip()
                        .lower()
                    )
                    suffix = (
                        "html"
                        if mime in {"text/html", "application/xhtml+xml"}
                        else "json"
                    )
                    member = f"responses/{entity['source']}/{hashlib.sha256(page['url'].encode()).hexdigest()[:24]}.{suffix}"
                    identity = page["url"] + "\n" + record_id
                    if (
                        not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", record_id)
                        or not page.get("records")
                        or page["id"]
                        != hashlib.sha256(identity.encode()).hexdigest()[:24]
                        or not response
                        or response.get("url") != page["url"]
                        or not response.get("content_type")
                        or response.get("body_member") != member
                        or "body_base64" in response
                        or response.get("sha256") != manifest["files"].get(member)
                    ):
                        raise ValueError("Invalid record response provenance")
                elif page.get("html_origin") == "api-rendered":
                    if (
                        not response
                        or response.get("url") != page["url"]
                        or not response.get("content_type")
                        or hashlib.sha256(
                            base64.b64decode(
                                response.get("body_base64", ""), validate=True
                            )
                        ).hexdigest()
                        != response.get("sha256")
                    ):
                        raise ValueError(
                            "API response does not match entity provenance"
                        )
                elif response is not None or "html_origin" in page:
                    raise ValueError("Unexpected API response provenance")
                evidence_pages.add(name)
        if len(evidence_pages) != manifest["evidence_pages"]:
            raise ValueError("Evidence count mismatch")
        if manifest["format_version"] >= 3:
            from pipelines.image_distribution import validate_image_bundle

            validate_image_bundle(archive, manifest, entities)
        if manifest["format_version"] == 4:
            from pipelines.observation_distribution import validate_observation_bundle

            validate_observation_bundle(archive, manifest)
        from pipelines.search_distribution import validate_search_bundle

        validate_search_bundle(archive, manifest, entities)
        return manifest


def notices(destination: Path) -> None:
    directories = set(
        run(
            [
                "go",
                "list",
                "-tags",
                "NODOWNLOAD",
                "-deps",
                "-f",
                "{{if .Module}}{{.Module.Dir}}{{end}}",
                "./cmd/pipelines",
            ],
            capture=True,
        ).splitlines()
    )
    directories.discard("")
    sections = [
        "Embedding model: sentence-transformers/all-MiniLM-L6-v2 (Apache-2.0).\n"
        "The adjacent model license applies to the model only.\n"
        "Source content retains its original attribution and terms.\n"
    ]
    for directory in sorted(directories):
        path = Path(directory)
        if path.resolve() == (ROOT / "cli").resolve():
            continue
        files = sorted(
            file
            for file in path.iterdir()
            if file.is_file()
            and file.name.upper().startswith(("LICENSE", "COPYING", "NOTICE"))
        )
        if not any(
            file.name.upper().startswith(("LICENSE", "COPYING")) for file in files
        ):
            raise ValueError(f"No dependency license found: {path.name}")
        sections.append(f"\nDependency: {path.name}\n")
        sections.extend(
            f"\n{file.name}\n{file.read_text(encoding='utf-8')}\n" for file in files
        )
    goroot = Path(run(["go", "env", "GOROOT"], capture=True).strip())
    sections.append(
        "\nGo runtime license\n" + (goroot / "LICENSE").read_text(encoding="utf-8")
    )
    destination.write_text("\n".join(sections), encoding="utf-8", newline="\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--skip-run",
        action="store_true",
        help="Cross compilation only; native CI still verifies every release",
    )
    args = parser.parse_args()
    manifest = verify_bundle(args.bundle)
    target = ROOT / "cli/internal/assets/data"
    if args.bundle.resolve() != (target / "dataset.zip").resolve():
        shutil.copyfile(args.bundle, target / "dataset.zip")
    notices(target / "THIRD-PARTY-NOTICES.txt")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    version = "data-" + manifest["dataset_id"][:16]
    run(
        [
            "go",
            "build",
            "-tags",
            "NODOWNLOAD",
            "-trimpath",
            "-ldflags",
            f"-s -w -X main.version={version}",
            "-o",
            str(args.output.resolve()),
            "./cmd/pipelines",
        ]
    )
    if not args.skip_run:
        subprocess.run([str(args.output.resolve()), "verify"], check=True)
    print(
        json.dumps(
            {
                "binary": str(args.output),
                "sha256": hashlib.sha256(args.output.read_bytes()).hexdigest(),
                "dataset_id": manifest["dataset_id"],
            }
        )
    )


if __name__ == "__main__":
    main()
