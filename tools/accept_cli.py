"""Exercise the built executable against its bundled data on a native runner."""

import base64
import hashlib
import json
import math
import subprocess
import sys
import tempfile
import zipfile
from array import array
from collections.abc import Callable
from pathlib import Path


def finite(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
    )


def bundle_evidence(archive: zipfile.ZipFile) -> dict[str, dict[str, str]]:
    return {
        item["id"]: {
            page["id"]: page["url"]
            for page in json.loads(
                archive.read("entities/" + item["id"].replace(":", "/") + ".json")
            )["evidence"]
        }
        for item in json.loads(archive.read("index.json"))
    }


def validate_observation_response(
    response: dict,
    manifest: dict,
    rows: dict,
    recipes: dict,
    evidence: dict[str, dict[str, str]],
    *,
    enabled: bool,
    source: str = "",
    limit: int = 20,
    image_sha256: str | None = None,
    images: dict | None = None,
) -> list[dict]:
    if response.get("dataset_id") != manifest["dataset_id"] or response.get(
        "query_type"
    ) != ("image_text" if image_sha256 else "text"):
        raise ValueError("Invalid observation query envelope")
    if response.get("match_status") not in {"candidates", "no_supported_match"}:
        raise ValueError("Invalid observation match status")
    if enabled:
        if (
            response.get("observations") is not True
            or response.get("match_status") != "no_supported_match"
            or response.get("calibration_status") != "uncalibrated"
        ):
            raise ValueError(
                "Generated observations must remain explicit uncalibrated suggestions"
            )
    elif (
        response.get("observations") is not None
        and response.get("observations") is not False
    ):
        raise ValueError("Source-only search enabled generated observations")
    if image_sha256 and response.get("query_image_sha256") != image_sha256:
        raise ValueError("Combined query image hash mismatch")
    items = response.get("results")
    if (
        not isinstance(items, list)
        or len(items) > limit
        or len({item["id"] for item in items}) != len(items)
    ):
        raise ValueError("Invalid observation result count")
    for item in items:
        identifier = item["id"]
        if (
            identifier not in evidence
            or item.get("source") != identifier.split(":", 1)[0]
            or (source and item["source"] != source)
        ):
            raise ValueError("Observation result has an unknown entity or filter leak")
        if (
            not finite(item.get("score"))
            or not finite(item.get("cosine"))
            or type(item.get("name_match")) is not bool
        ):
            raise ValueError("Observation result has invalid scores")
        matches = item.get("matches")
        if not isinstance(matches, list) or not matches:
            raise ValueError("Observation result has no contributions")
        text = [match for match in matches if match.get("channel") != "image"]
        visual = [match for match in matches if match.get("channel") == "image"]
        if len(text) != 1 or len(visual) > 1 or (visual and image_sha256 is None):
            raise ValueError("Invalid observation contribution channels")
        text_score = item["cosine"] + (2 if item["name_match"] else 0)
        if (
            not finite(text[0].get("score"))
            or abs(text[0]["score"] - text_score) > 1e-6
            or (image_sha256 is None and abs(item["score"] - text_score) > 1e-6)
        ):
            raise ValueError(
                "Text contribution does not match its result ranking score"
            )
        for match in matches:
            if (
                not finite(match.get("score"))
                or evidence[identifier].get(match.get("evidence_id"))
                != match.get("url")
                or not match.get("url")
            ):
                raise ValueError("Contribution has invalid canonical evidence or score")
            channel = match.get("channel")
            if channel == "text":
                if match.get("origin") == "generated" or match.get("observation_id"):
                    raise ValueError(
                        "Generated text was presented as a source contribution"
                    )
            elif channel == "image":
                picture = (images or {}).get(match.get("media_id"))
                if (
                    picture is None
                    or picture["vector_index"] is None
                    or match.get("model_sha256") != manifest["image"]["model_sha256"]
                    or not any(
                        ref["entity_id"] == identifier
                        and ref["evidence_id"] == match.get("evidence_id")
                        for ref in picture["references"]
                    )
                ):
                    raise ValueError("Invalid image contribution identity")
            else:
                row = rows.get(match.get("observation_id"))
                if not enabled or channel not in {"ocr", "description"} or row is None:
                    raise ValueError("Unexpected generated contribution")
                recipe = recipes[row["recipe_sha256"]]
                if (
                    row["kind"] != channel
                    or match.get("origin") != "generated"
                    or match.get("recipe_sha256") != row["recipe_sha256"]
                    or match.get("model_id") != recipe["model_id"]
                    or match.get("model_revision") != recipe["model_revision"]
                    or match.get("embedding_model_sha256")
                    != manifest["observations"]["embedding_model_sha256"]
                    or not any(
                        ref["entity_id"] == identifier
                        and ref["media_id"] == match.get("media_id")
                        and ref["evidence_id"] == match.get("evidence_id")
                        for ref in row["references"]
                    )
                ):
                    raise ValueError("Generated contribution has invalid provenance")
    return items


def validate_observation_export(
    response: dict, manifest: dict, entity_id: str, expected: dict, recipes: dict
) -> None:
    if (
        response.get("dataset_id") != manifest["dataset_id"]
        or response.get("entity_id") != entity_id
        or len(response.get("observations", [])) != 1
    ):
        raise ValueError("Invalid observation inspection envelope")
    row = response["observations"][0]

    def canonical(value: object) -> str:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )

    if canonical(row) != canonical(expected) or canonical(
        response.get("recipes")
    ) != canonical({expected["recipe_sha256"]: recipes[expected["recipe_sha256"]]}):
        raise ValueError("Observation inspection changed generated content or recipe")
    if row["origin"] != "generated" or not any(
        ref["entity_id"] == entity_id for ref in row["references"]
    ):
        raise ValueError("Observation inspection has invalid ownership")
    if row["confidence"] is not None and (
        not finite(row["confidence"]) or not 0 <= row["confidence"] <= 1
    ):
        raise ValueError("Invalid generated confidence")
    if row["kind"] == "description" and (
        row["regions"] or row["confidence"] is not None
    ):
        raise ValueError("Descriptions cannot claim measured image confidence")
    for region in row["regions"]:
        score, polygon = region["confidence"], region["polygon"]
        if (
            (score is not None and (not finite(score) or not 0 <= score <= 1))
            or len(polygon) != 4
            or any(
                len(point) != 2 or any(not finite(v) or not 0 <= v <= 1 for v in point)
                for point in polygon
            )
        ):
            raise ValueError("Invalid generated region confidence or polygon")


def source_probe_scores(archive: zipfile.ZipFile, manifest: dict) -> dict[str, float]:
    def floats(body: bytes) -> array:
        values = array("f")
        values.frombytes(body)
        if sys.byteorder != "little":
            values.byteswap()
        return values

    dimensions = manifest["model"]["dimensions"]
    query = floats(archive.read("probes.f32")[: dimensions * 4])
    vectors = floats(archive.read("vectors.f32"))
    index = json.loads(archive.read("index.json"))
    scores: dict[str, float] = {}
    for position, chunk in enumerate(json.loads(archive.read("chunks.json"))):
        identifier = index[chunk["entity"]]["id"]
        cosine = sum(
            a * b
            for a, b in zip(
                query,
                vectors[position * dimensions : (position + 1) * dimensions],
                strict=True,
            )
        )
        scores[identifier] = max(scores.get(identifier, -math.inf), cosine)
    return scores


def accept_observations(
    run: Callable[..., bytes], archive: zipfile.ZipFile, manifest: dict
) -> None:
    rows = {
        row["id"]: row for row in json.loads(archive.read("observations/index.json"))
    }
    recipes = json.loads(archive.read("observations/recipes.json"))
    evidence = bundle_evidence(archive)
    probe = json.loads(archive.read("probes.json"))[0]
    baseline_args = ("search", "--mode", "vector", "--limit", "3", probe)
    baseline = json.loads(run(*baseline_args))
    found = validate_observation_response(
        baseline, manifest, rows, recipes, evidence, enabled=False, limit=3
    )
    expected_scores = source_probe_scores(archive, manifest)
    if (
        len(found) != min(3, len(expected_scores))
        or not found
        or any(
            abs(item["cosine"] - expected_scores[item["id"]]) > 0.001
            or abs(item["score"] - item["cosine"]) > 1e-6
            or item["name_match"]
            for item in found
        )
    ):
        raise ValueError("Default search no longer matches the original source vectors")
    if any(
        right["cosine"] > left["cosine"] + 0.001
        for left, right in zip(found, found[1:])
    ):
        raise ValueError("Default source ranking changed vector order")
    if any(
        value > found[-1]["cosine"] + 0.001
        for identifier, value in expected_scores.items()
        if identifier not in {item["id"] for item in found}
    ):
        raise ValueError("Default source ranking omitted a higher scoring entity")
    selected: dict[str, dict] = {}
    for row in rows.values():
        selected.setdefault(row["kind"], row)
    if not selected:
        entity_id = next(iter(evidence))
        inspected = json.loads(run("observations", entity_id))
        if inspected != {
            "dataset_id": manifest["dataset_id"],
            "entity_id": entity_id,
            "observations": [],
            "recipes": {},
        }:
            raise ValueError("Empty observation inspection mismatch")
        response = json.loads(run("search", "--observations", "--limit", "3", probe))
        validate_observation_response(
            response, manifest, rows, recipes, evidence, enabled=True, limit=3
        )
    chunks = json.loads(archive.read("observations/chunks.json"))
    images = {row["id"]: row for row in json.loads(archive.read("image/index.json"))}
    for row in selected.values():
        entity_id = row["references"][0]["entity_id"]
        inspected = json.loads(run("observations", "--id", row["id"], entity_id))
        validate_observation_export(inspected, manifest, entity_id, row, recipes)
        query = next(
            chunk["text"] for chunk in chunks if chunk["observation_id"] == row["id"]
        )
        complete_query = len(query) <= 1000
        query = query[:1000]
        source = entity_id.split(":", 1)[0]
        response = json.loads(
            run(
                "search",
                "--observations",
                "--mode",
                "vector",
                "--source",
                source,
                "--limit",
                "3",
                query,
            )
        )
        matches = validate_observation_response(
            response,
            manifest,
            rows,
            recipes,
            evidence,
            enabled=True,
            source=source,
            limit=3,
        )
        if not matches:
            raise ValueError("Generated text self-query returned no results")
        if complete_query and matches[0]["cosine"] < 0.999:
            raise ValueError(
                "Generated text self-query did not find its embedded chunk"
            )
        picture = images[row["references"][0]["media_id"]]
        body = archive.read(picture["preview"]["member"])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "preview.jpg"
            path.write_bytes(body)
            response = json.loads(
                run(
                    "search",
                    "--observations",
                    "--image",
                    str(path),
                    "--source",
                    source,
                    "--limit",
                    "3",
                    query,
                )
            )
            validate_observation_response(
                response,
                manifest,
                rows,
                recipes,
                evidence,
                enabled=True,
                source=source,
                limit=3,
                image_sha256=hashlib.sha256(body).hexdigest(),
                images=images,
            )
            if contains_query_path(response, str(path)):
                raise ValueError("Generated combined search leaked the query path")
    if json.loads(run(*baseline_args)) != baseline:
        raise ValueError("Observation queries changed default source search")


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
        if manifest.get("observations"):
            assert info.get("observations_available") is True
            assert info.get("observations") == manifest["observations"]
            accept_observations(run, archive, manifest)
        else:
            assert not info.get("observations_available")
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
