import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest
from accept_cli import (
    accept_empty_gallery,
    contains_query_path,
    validate_image_response,
)


def contract() -> dict:
    return {
        "dataset_id": "bundle",
        "image": {
            "vectors": 0,
            "model_sha256": "a" * 64,
            "search": {"calibration": None},
        },
    }


def envelope() -> dict:
    return {
        "dataset_id": "bundle",
        "query_type": "image",
        "query_image_sha256": "hash",
        "match_status": "no_supported_match",
        "calibration_status": "uncalibrated",
        "results": [],
    }


def test_windows_query_path_leaks_are_detected_after_json_decoding() -> None:
    path = r"C:\Users\Researcher\query.png"
    decoded = json.loads(json.dumps({"results": [{"debug": path}]}))
    assert contains_query_path(decoded, path)
    assert contains_query_path({"query_path": path.replace("\\", "/")}, path)
    assert not contains_query_path({"query_image_sha256": "abc"}, path)


def test_uncalibrated_queries_cannot_claim_accepted_matches() -> None:
    response = envelope()
    assert validate_image_response(response, contract(), "hash", False) == []
    response["match_status"] = "candidates"
    with pytest.raises(ValueError, match="uncalibrated"):
        validate_image_response(response, contract(), "hash", False)


def test_combined_queries_require_text_scores_and_contributions() -> None:
    response = envelope()
    response["query_type"] = "image_text"
    item: dict = {
        "id": "source:one",
        "source": "source",
        "score": 0.02,
        "cosine": None,
        "name_match": False,
        "matches": [],
    }
    response["results"] = [item]
    with pytest.raises(ValueError, match="text contribution"):
        validate_image_response(response, contract(), "hash", True)
    item["cosine"] = 0.5
    item["matches"] = [
        {
            "channel": "text",
            "score": 0.5,
            "evidence_id": "page",
            "url": "https://source.test/page",
        }
    ]
    assert validate_image_response(response, contract(), "hash", True) == [item]


def test_empty_gallery_is_exercised_with_an_embedded_probe() -> None:
    body = io.BytesIO()
    expected = b"embedded probe bytes"
    with zipfile.ZipFile(body, "w") as archive:
        archive.writestr(
            "image/probes.json",
            json.dumps([{"image_member": "image/probes/sample.png"}]),
        )
        archive.writestr("image/probes/sample.png", expected)
    calls = []

    def run(*args: str) -> bytes:
        assert args[:2] == ("search", "--image")
        assert Path(args[2]).read_bytes() == expected
        calls.append(args)
        response = envelope()
        response["query_image_sha256"] = hashlib.sha256(expected).hexdigest()
        return json.dumps(response).encode()

    with zipfile.ZipFile(body) as archive:
        accept_empty_gallery(run, archive, contract())
    assert len(calls) == 1
