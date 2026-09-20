"""Audit trial coverage, full content retention, and optional offline CLI exports."""

import argparse
import json
import subprocess
from collections import Counter
from pathlib import Path

from bs4 import BeautifulSoup

from pipelines.archive import Archive
from pipelines.snapshot import load_snapshot
from pipelines.sources.militaryperiscope import SUBJECTS, MilitaryPeriscope, payload


def text(value: str) -> str:
    return " ".join(BeautifulSoup(value, "html.parser").get_text(" ").split())


def content_fragments(blocks: list) -> list[str]:
    result = []
    for block in blocks:
        kind, value = block["type"], block["value"]
        if kind in {"content", "paragraph"}:
            result.append(value)
        elif kind == "table":
            result.extend(cell for row in value["data"] for cell in row if cell)
        elif kind == "image":
            result.extend(
                value[key] for key in ("title", "source", "caption") if value.get(key)
            )
        elif kind == "images":
            result.extend(
                content_fragments(
                    [{"type": "image", "value": item["image"]} for item in value]
                )
            )
        elif isinstance(value, list):
            result.extend(content_fragments(value))
        else:
            result.extend(
                value[key] for key in ("header", "subheader", "style") if value.get(key)
            )
            result.extend(content_fragments(value["body"]))
    return result


def audit(root: Path, binary: Path | None = None) -> dict:
    source = MilitaryPeriscope()
    source_dir = root / source.id
    manifest, entities = load_snapshot(source_dir)
    archive = Archive(source_dir / "archives" / manifest["archive"])
    fragments_checked = 0
    try:
        pages = {p["url"]: p for p in archive.pages("saved")}
        source.prepare((url, archive.body(p)) for url, p in pages.items())
        expected = {
            str(n["id"]) for n in source.nodes.values() if n["page_type"] in SUBJECTS
        }
        assert expected == {e["source_id"] for e in entities}, (
            "Trial subjects missing or duplicated"
        )
        actual_pages = {p["url"] for e in entities for p in e["evidence"]}
        assert actual_pages == source.subjects.keys() - source.restricted, (
            "Full sections missing"
        )
        for entity in entities:
            for page in entity["evidence"]:
                response = archive.body(pages[page["url"]])
                _, props = payload(response)
                rendered = (
                    source_dir
                    / "published/snapshots"
                    / manifest["snapshot"]
                    / "html"
                    / (page["id"] + ".html")
                ).read_text(encoding="utf-8")
                retained = text(rendered)
                for fragment in content_fragments(
                    props.get("section") or props["content"]
                ):
                    assert text(fragment) in retained, (
                        f"Missing content in {entity['id']}"
                    )
                    fragments_checked += 1
                if binary:
                    for format, expected_bytes in [
                        ("source", response),
                        ("markdown", page["markdown"].encode()),
                        ("html", rendered.encode()),
                    ]:
                        exported = subprocess.run(
                            [
                                str(binary.resolve()),
                                "get",
                                "--format",
                                format,
                                "--evidence",
                                page["id"],
                                entity["id"],
                            ],
                            check=True,
                            capture_output=True,
                            timeout=120,
                        ).stdout
                        assert exported == expected_bytes, (
                            f"CLI export mismatch: {entity['id']} {format}"
                        )
        return {
            "archive": manifest["archive"],
            "snapshot": manifest["snapshot"],
            "captured_responses": len(pages),
            "captured_bytes": sum(
                (archive.path / p["file"]).stat().st_size for p in pages.values()
            ),
            "catalog_nodes": len(source.nodes),
            "trial_entities": len(entities),
            "full_evidence_pages": len(actual_pages),
            "content_fragments_checked": fragments_checked,
            "subjects_by_type": dict(
                Counter(
                    n["page_type"]
                    for n in source.nodes.values()
                    if n["page_type"] in SUBJECTS
                )
            ),
            "kinds": dict(Counter(e["kind"] for e in entities)),
            "subscription_only_sections": sorted(source.restricted),
            "cli_export_pages_checked": len(actual_pages) if binary else 0,
            "complete_accessible_trial": True,
        }
    finally:
        archive.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = audit(args.root, args.binary)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
