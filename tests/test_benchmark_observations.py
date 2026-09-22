import hashlib
import io
import json
import zipfile
from copy import deepcopy
from pathlib import Path

import benchmark_observations
import pytest
from benchmark_observations import benchmark, summarize, validate_response
from test_accept_cli import (
    observation_archive,
    observation_contract,
    observation_response,
)


def test_ranked_improvement_does_not_imply_accepted_identification() -> None:
    rows = [
        {
            "expected_ids": ["a:one"],
            "photo_group": "one",
            "source": {"rank": None, "match_status": "candidates"},
            "observations": {"rank": 1, "match_status": "no_supported_match"},
        },
        {
            "expected_ids": [],
            "photo_group": "negative",
            "source": {"rank": 1, "match_status": "candidates"},
            "observations": {"rank": 1, "match_status": "no_supported_match"},
        },
    ]
    report = summarize(rows)
    assert report["improved_ranks"] == 1
    assert report["observations_ranked_top1"]["rate"] == 1
    assert report["observations_accepted_top1"]["rate"] == 0
    assert report["observations_false_acceptance"]["rate"] == 0


@pytest.mark.parametrize(
    "failure",
    [
        "accepted",
        "recipe",
        "foreign_entity",
        "channel",
        "source",
        "duplicate",
        "missing_contributions",
        "nonfinite",
        "canonical_url",
        "implicit_opt_in",
    ],
)
def test_invalid_generated_evidence_cannot_be_scored(failure: str) -> None:
    manifest, rows, recipes, evidence, _ = observation_contract()
    arguments = (manifest, rows, recipes, evidence)
    response = observation_response()
    validate_response(response, *arguments, enabled=True, source="source")
    enabled = True
    if failure == "accepted":
        response["match_status"] = "candidates"
    elif failure == "recipe":
        response["results"][0]["matches"][0]["recipe_sha256"] = "other"
    elif failure == "foreign_entity":
        response["results"][0]["id"] = "source:other"
    elif failure == "channel":
        response["results"][0]["matches"][0]["channel"] = "image"
    elif failure == "source":
        response["results"][0]["source"] = "b"
    elif failure == "duplicate":
        response["results"].append(deepcopy(response["results"][0]))
    elif failure == "missing_contributions":
        del response["results"][0]["matches"]
    elif failure == "nonfinite":
        response["results"][0]["cosine"] = float("inf")
    elif failure == "canonical_url":
        response["results"][0]["matches"][0]["url"] = "https://other.test/page"
    elif failure == "implicit_opt_in":
        response = observation_response(enabled=False)
        response["observations"] = True
        enabled = False
    with pytest.raises(ValueError):
        validate_response(response, *arguments, enabled=enabled, source="source")


def benchmark_fixture(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str = ""
) -> tuple[Path, Path, Path]:
    manifest = observation_contract()[0]
    manifest.update(format_version=4, files={})
    manifest["image"]["gallery_sha256"] = "gallery"
    manifest["observations"]["search"] = {"aggregation": "max-v1", "calibration": None}
    with zipfile.ZipFile(io.BytesIO(observation_archive())) as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    for name in ("observations/index.json", "observations/recipes.json"):
        manifest["files"][name] = hashlib.sha256(members[name]).hexdigest()
    manifest["observations"]["index_sha256"] = manifest["files"][
        "observations/index.json"
    ]
    if failure in {"index", "recipes"}:
        members[f"observations/{failure}.json"] += b" "
    elif failure == "identity":
        manifest["observations"]["index_sha256"] = "wrong"
    elif failure == "legacy":
        manifest["format_version"] = 3
    bundle = tmp_path / "bundle.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        for name, body in members.items():
            archive.writestr(name, body)
    monkeypatch.setattr(
        benchmark_observations, "gallery_contract", lambda *_: (manifest, {})
    )
    gallery = tmp_path / "gallery.json"
    gallery.write_bytes(b"{}")
    fixture = tmp_path / "queries.json"
    fixture.write_text(
        json.dumps(
            {
                "gallery_fixture": str(gallery),
                "gallery_fixture_sha256": hashlib.sha256(
                    gallery.read_bytes()
                ).hexdigest(),
                "limitations": ["synthetic fixture"],
                "cases": [
                    {
                        "id": split,
                        "split": split,
                        "source": "source",
                        "query": "radar",
                        "task": "ocr",
                        "photo_group": split,
                        "expected_ids": ["source:one"],
                    }
                    for split in ("development", "evaluation")
                ],
            }
        )
    )
    binary = tmp_path / "binary"
    binary.write_bytes(b"frozen executable")
    return binary, bundle, fixture


@pytest.mark.parametrize("failure", ["index", "recipes", "identity", "legacy"])
def test_benchmark_rejects_corrupt_or_missing_extension_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    binary, bundle, fixture = benchmark_fixture(tmp_path, monkeypatch, failure)

    def never_run(*args: str) -> dict:
        pytest.fail("Invalid bundle must be rejected before executable runs")

    with pytest.raises(ValueError):
        benchmark(binary, bundle, fixture, "development", runner=never_run)


def test_benchmark_freezes_identity_and_exercises_separate_search_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary, bundle, fixture = benchmark_fixture(tmp_path, monkeypatch)
    calls: list[tuple[str, ...]] = []

    def run(*args: str) -> dict:
        calls.append(args)
        if args == ("info",):
            return {"dataset_id": "bundle", "observations_available": True}
        return observation_response(enabled="--observations" in args)

    with pytest.raises(ValueError, match="frozen development"):
        benchmark(binary, bundle, fixture, "evaluation", runner=run)
    assert not calls
    report = benchmark(binary, bundle, fixture, "development", runner=run)
    assert len(report["cases"]) == 2
    assert len([call for call in calls if "--observations" in call]) == 2
    assert (
        len(
            [
                call
                for call in calls
                if call[0] == "search" and "--observations" not in call
            ]
        )
        == 2
    )
    assert report["metrics"]["global"]["observations_accepted_top1"]["rate"] == 0
    selection = tmp_path / "selection.json"
    selection.write_text(
        json.dumps(
            {
                "identity": report["identity"],
                "binary_sha256": report["binary_sha256"],
                "selection_split": "development",
            }
        )
    )
    result = benchmark(
        binary, bundle, fixture, "evaluation", selection=selection, runner=run
    )
    assert result["cases"][0]["id"] == "evaluation"
    binary.write_bytes(b"changed executable")
    calls.clear()
    with pytest.raises(ValueError, match="do not match"):
        benchmark(
            binary, bundle, fixture, "evaluation", selection=selection, runner=run
        )
    assert not calls
