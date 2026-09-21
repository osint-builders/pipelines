"""Exercise the built executable against its bundled data on a native runner."""

import base64
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Callable
from pathlib import Path


def contains_query_path(value: object, path: str) -> bool:
    if isinstance(value, str):
        return any(
            spelling in value
            for spelling in {path, path.replace("\\", "/"), path.replace("/", "\\")}
        )
    if isinstance(value, dict):
        return any(
            contains_query_path(key, path) or contains_query_path(item, path)
            for key, item in value.items()
        )
    if isinstance(value, list):
        return any(contains_query_path(item, path) for item in value)
    return False


def validate_image_response(
    response: dict,
    manifest: dict,
    image_sha256: str,
    combined: bool,
    *,
    source: str = "",
    limit: int = 10,
) -> list[dict]:
    def finite(value: object) -> bool:
        return (
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(value)
        )

    if (
        response.get("dataset_id") != manifest["dataset_id"]
        or response.get("query_image_sha256") != image_sha256
        or response.get("query_type") != ("image_text" if combined else "image")
        or response.get("match_status") not in {"candidates", "no_supported_match"}
    ):
        raise ValueError("Invalid image query envelope")
    if manifest["image"]["search"]["calibration"] is None and (
        response.get("match_status") != "no_supported_match"
        or response.get("calibration_status") != "uncalibrated"
    ):
        raise ValueError("An uncalibrated image query cannot accept an identity")
    items = response["results"]
    if not isinstance(items, list) or len(items) > limit:
        raise ValueError("Invalid image result count")
    if len({item["id"] for item in items}) != len(items):
        raise ValueError("Duplicate entity results")
    for item in items:
        if (source and item["source"] != source) or item["id"].split(":", 1)[0] != item[
            "source"
        ]:
            raise ValueError("Source filter leak")
        if not finite(item.get("score")):
            raise ValueError("Invalid result score")
        matches = item["matches"]
        visual = [match for match in matches if match["channel"] == "image"]
        text = [match for match in matches if match["channel"] == "text"]
        if len(matches) != len(visual) + len(text) or len(visual) > 1:
            raise ValueError("Invalid contribution channels")
        if combined:
            if len(text) != 1 or not finite(item.get("cosine")):
                raise ValueError(
                    "Combined result requires a text contribution and cosine"
                )
        elif (
            len(visual) != 1 or text or item["cosine"] is not None or item["name_match"]
        ):
            raise ValueError(
                "Image-only result has invalid contributions or text fields"
            )
        for match in matches:
            if (
                not finite(match.get("score"))
                or not match.get("evidence_id")
                or not match.get("url")
            ):
                raise ValueError("Contribution is missing its score or evidence")
        for match in visual:
            if (
                not match.get("media_id")
                or match.get("model_sha256") != manifest["image"]["model_sha256"]
            ):
                raise ValueError(
                    "Image contribution has no matching media/model identity"
                )
    return items


def accept_empty_gallery(
    run: Callable[..., bytes], archive: zipfile.ZipFile, manifest: dict
) -> None:
    assert manifest["image"]["vectors"] == 0
    probes = json.loads(archive.read("image/probes.json"))
    probe = archive.read(probes[0]["image_member"])
    with tempfile.TemporaryDirectory() as directory:
        picture = Path(directory) / "probe.png"
        picture.write_bytes(probe)
        response = json.loads(run("search", "--image", str(picture)))
        found = validate_image_response(
            response, manifest, hashlib.sha256(probe).hexdigest(), False
        )
        assert not found and not contains_query_path(response, str(picture))


def accept(binary: Path, bundle: Path) -> None:
    def run(*args: str) -> bytes:
        return subprocess.run(
            [str(binary.resolve()), *args], capture_output=True, check=True, timeout=120
        ).stdout

    info = json.loads(run("info"))
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert info["dataset_id"] == manifest["dataset_id"]
        search = json.loads(
            run(
                "search",
                "--mode",
                "vector",
                "--limit",
                "3",
                "detection of aircraft approaching an airport",
            )
        )
        assert search["mode"] == "vector" and search["results"]
        assert not any(result["name_match"] for result in search["results"])
        identifier = search["results"][0]["id"]
        index = json.loads(archive.read("index.json"))
        example = "radartutorial:8bdc6ce92fea3ca62de71395"
        if any(document["id"] == example for document in index):
            search = json.loads(
                run(
                    "search",
                    "--source",
                    "radartutorial",
                    "--limit",
                    "3",
                    "russian cheeseboard",
                )
            )
            assert search["results"][0]["id"] == example, search["results"]
            identifier = example
        assert info["entities"] == len(index)
        assert all(item["kind"] != "article" for item in index)
        samples = {item["source"]: item["id"] for item in index}
        samples[identifier.split(":", 1)[0]] = identifier
        for identifier in samples.values():
            document = json.loads(run("get", identifier))
            original = json.loads(
                archive.read("entities/" + identifier.replace(":", "/") + ".json")
            )
            assert all(
                document[key] == value
                for key, value in original.items()
                if key != "evidence"
            )
            assert len(document["evidence"]) == len(original["evidence"])
            for page, exported in zip(
                original["evidence"], document["evidence"], strict=True
            ):
                assert all(exported[key] == value for key, value in page.items())
                assert (
                    run(
                        "get",
                        "--format",
                        "markdown",
                        "--evidence",
                        page["id"],
                        identifier,
                    ).decode()
                    == page["markdown"]
                )
                html = run(
                    "get", "--format", "html", "--evidence", page["id"], identifier
                )
                assert html == archive.read(
                    "html/" + original["source"] + "/" + page["id"] + ".html"
                )
                assert hashlib.sha256(html).hexdigest() == page["html_sha256"]
                captured = run(
                    "get", "--format", "source", "--evidence", page["id"], identifier
                )
                response = page.get("source_response")
                if response:
                    expected = (
                        archive.read(response["body_member"])
                        if "body_member" in response
                        else base64.b64decode(response["body_base64"])
                    )
                    assert captured == expected
                    assert hashlib.sha256(captured).hexdigest() == response["sha256"]
                else:
                    assert captured == html
        neighbors = json.loads(run("similar", "--limit", "3", identifier))
        assert all(item["id"] != identifier for item in neighbors["results"])
        assert len({item["id"] for item in neighbors["results"]}) == len(
            neighbors["results"]
        )
        bad = subprocess.run(
            [str(binary.resolve()), "get", "missing:id"], capture_output=True
        )
        assert bad.returncode != 0 and json.loads(bad.stderr)["error"]
        if manifest.get("image"):
            assert info["image_available"] and info["image"] == manifest["image"]
            assert info["image_model"]["sha256"] == manifest["image"]["model_sha256"]
            records = json.loads(archive.read("image/index.json"))
            record = next(
                (
                    item
                    for item in records
                    if item["vector_index"] is not None and item["preview"] is not None
                ),
                None,
            )
            if record is None:
                accept_empty_gallery(run, archive, manifest)
            else:
                entity_id = record["references"][0]["entity_id"]
                expected = archive.read(record["preview"]["member"])
                assert (
                    hashlib.sha256(expected).hexdigest() == record["preview"]["sha256"]
                )
                metadata = json.loads(run("media", "--id", record["id"], entity_id))
                assert len(metadata) == 1 and metadata[0]["id"] == record["id"]
                assert metadata[0]["sha256"] == record["sha256"]
                assert metadata[0]["preview"] == record["preview"]
                with tempfile.TemporaryDirectory() as directory:
                    preview = Path(directory) / "preview.jpg"
                    run(
                        "media",
                        "--id",
                        record["id"],
                        "--output",
                        str(preview),
                        entity_id,
                    )
                    assert preview.read_bytes() == expected
                    existing = subprocess.run(
                        [
                            str(binary.resolve()),
                            "media",
                            "--id",
                            record["id"],
                            "--output",
                            str(preview),
                            entity_id,
                        ],
                        capture_output=True,
                        timeout=120,
                    )
                    assert existing.returncode != 0 and preview.read_bytes() == expected
                    for extra in (
                        [],
                        [next(e["title"] for e in index if e["id"] == entity_id)],
                    ):
                        response = json.loads(
                            run(
                                "search",
                                "--image",
                                str(preview),
                                "--source",
                                record["source"],
                                *extra,
                            )
                        )
                        found = validate_image_response(
                            response,
                            manifest,
                            hashlib.sha256(expected).hexdigest(),
                            bool(extra),
                            source=record["source"],
                        )
                        assert not contains_query_path(response, str(preview))
                        assert found
                        assert entity_id in {item["id"] for item in found[:5]}
                        assert any(
                            match["channel"] == "image" and match["media_id"]
                            for item in found
                            for match in item["matches"]
                        )
                        if not extra:
                            assert all(
                                item["cosine"] is None and not item["name_match"]
                                for item in found
                            )
    print(
        json.dumps(
            {
                "accepted": str(binary),
                "dataset_id": info["dataset_id"],
                "entities_checked": samples,
            }
        )
    )


if __name__ == "__main__":
    accept(Path(sys.argv[1]), Path(sys.argv[2]))
