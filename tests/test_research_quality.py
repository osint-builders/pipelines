import copy
import json
from pathlib import Path

import pytest
import research_quality
from research_quality import ranking_summary, text_summary


def test_ranking_separates_suggestions_from_accepted_identifications() -> None:
    fixture = {
        "cases": [
            {
                "id": "view",
                "query": {"image_id": "photo"},
                "expected_ids": ["source:tank"],
                "task": "unseen_view",
            }
        ],
        "media": [{"id": "photo", "photo_group": "independent-photo"}],
    }
    captures: dict = {
        "cases": [
            {
                "id": "view",
                "scope": "global",
                "response": {
                    "results": [{"id": "source:tank"}],
                    "calibration_status": "uncalibrated",
                    "match_status": "no_supported_match",
                },
            }
        ]
    }
    summary = ranking_summary(fixture, captures)["global"]["mode:image"]
    assert summary["raw_ranking"]["top1"]["rate"] == 1
    assert summary["accepted"]["top1"]["rate"] == 0
    assert "false_acceptance" not in summary["raw_ranking"]
    assert summary["raw_ranking"]["top1"]["wilson95"] is not None
    captures["cases"][0]["response"]["match_status"] = "candidates"
    with pytest.raises(ValueError, match="must not assert identification"):
        ranking_summary(fixture, captures)


def test_text_checks_recompute_passes_instead_of_trusting_flags() -> None:
    fixture = [
        {"id": "first", "max_rank": 1},
        {"id": "optional", "max_rank": 2, "required": False},
    ]
    report = {
        "results": [
            {**fixture[0], "rank": 2, "passed": True},
            {**fixture[1], "rank": 1, "passed": True},
        ],
        "ok": True,
    }
    summary = text_summary(report, fixture)
    assert summary["required_passed"] == 0
    assert summary["recall_at_5_hits"] == 2
    report["skipped"] = [fixture[0]]
    with pytest.raises(ValueError, match="without skips"):
        text_summary(report, fixture)


def test_research_text_contract_rejects_a_required_regression(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path
    directory = root / "tests/fixtures"
    directory.mkdir(parents=True)
    cases = [{"id": "first", "max_rank": 1}]
    path = directory / "retrieval.json"
    path.write_text(json.dumps(cases))
    (directory / "search_acceptance.json").write_text(
        json.dumps({"baseline": {"dataset_id": "old"}})
    )
    monkeypatch.setattr(research_quality, "ROOT", root)
    baseline: dict = {
        "cases": {"sha256": research_quality.sha256(path)},
        "evaluation": {"dataset_id": "old", "results": [{**cases[0], "rank": 1}]},
    }
    current = copy.deepcopy(baseline["evaluation"])
    current["dataset_id"] = "new"
    research_quality.validate_text(current, baseline, "new")
    current["results"][0]["rank"] = 2
    with pytest.raises(ValueError, match="regression failed"):
        research_quality.validate_text(current, baseline, "new")


def test_research_quality_fails_closed_for_missing_evidence() -> None:
    with pytest.raises(ValueError, match="incomplete"):
        research_quality.evaluate({})


def test_rejected_query_stays_in_recall_denominator() -> None:
    fixture = {
        "cases": [
            {
                "id": "bad",
                "query": {"image_id": "photo"},
                "expected_ids": ["source:tank"],
                "task": "unseen_view",
            }
        ],
        "media": [{"id": "photo", "photo_group": "photo-one"}],
    }
    capture = {"cases": [{"id": "bad", "scope": "global", "failure": {"exit_code": 1}}]}
    summary = ranking_summary(fixture, capture)["global"]["mode:image"]
    assert summary["raw_ranking"]["positive_cases"] == 1
    assert summary["raw_ranking"]["query_failures"] == 1
    assert summary["raw_ranking"]["recall_at_5"]["rate"] == 0
