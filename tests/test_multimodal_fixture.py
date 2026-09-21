import hashlib
import json
import re
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "multimodal.json"
ROOT = Path(__file__).resolve().parents[1]


def test_multimodal_photo_groups_cannot_leak_into_gallery() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    media = {item["id"]: item for item in fixture["media"]}
    assert len(media) == len(fixture["media"])
    groups: dict[str, str] = {}
    hashes: dict[str, str] = {}
    for item in media.values():
        assert item["split"] in {"gallery", "development", "evaluation"}
        assert groups.setdefault(item["photo_group"], item["split"]) == item["split"]
        assert item["url"].startswith("https://")
        assert "@" not in item["url"]
        if item["status"] != "captured":
            assert item["review_status"] == "pending_visual_review"
            assert "sha256" not in item
            continue
        assert re.fullmatch(r"[0-9a-f]{64}", item["sha256"])
        assert (
            hashes.setdefault(item["sha256"], item["photo_group"])
            == item["photo_group"]
        )
        if "crop_from" in item:
            parent = media[item["crop_from"]]
            assert item["photo_group"] == parent["photo_group"]
            assert item["split"] == parent["split"]
            left, top, right, bottom = item["crop_box_xyxy"]
            assert 0 <= left < right <= parent["width"]
            assert 0 <= top < bottom <= parent["height"]
            assert (item["width"], item["height"]) == (right - left, bottom - top)

    case_ids = set()
    for case in fixture["cases"]:
        assert case["id"] not in case_ids
        case_ids.add(case["id"])
        assert case["split"] in {"development", "evaluation"}
        expected = case["expected_ids"]
        confusable = case.get("confusable_ids", [])
        assert not set(expected).intersection(confusable)
        assert all(
            re.fullmatch(r"[a-z]+:[a-z0-9-]+", key) for key in expected + confusable
        )
        assert bool(expected) == (case["task"] != "no_match")
        if "image_id" not in case["query"]:
            continue
        assert "mode" not in case["query"]
        query = media[case["query"]["image_id"]]
        assert query["split"] == case["split"]
        assert set(expected) <= set(query["entity_ids"])
        if case["status"] == "ready":
            assert query["status"] == "captured"
            if expected:
                assert case.get("reference_media_ids")
        for reference_id in case.get("reference_media_ids", []):
            reference = media[reference_id]
            assert reference["split"] == "gallery"
            assert reference["photo_group"] != query["photo_group"]
            assert set(reference["entity_ids"]).intersection(expected)
            if case["status"] == "ready":
                assert reference["status"] == "captured"


def test_multimodal_cached_bytes_match_frozen_manifest() -> None:
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    captured = [item for item in fixture["media"] if item["status"] == "captured"]
    if not any((ROOT / item["local_path"]).exists() for item in captured):
        pytest.skip("M1 image bytes are an optional local cache, outside Git")
    for item in captured:
        path = ROOT / item["local_path"]
        assert path.resolve().is_relative_to(
            (ROOT / "build" / "m1" / "media").resolve()
        )
        assert path.exists(), item["id"]
        data = path.read_bytes()
        assert len(data) == item["bytes"], item["id"]
        assert hashlib.sha256(data).hexdigest() == item["sha256"], item["id"]
