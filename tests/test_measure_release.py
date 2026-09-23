import json
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from measure_release import (
    CONTRACT,
    MODES,
    functional_acceptance,
    native_target,
    query_commands,
    reference_checks,
    resource_checks,
)


def candidate() -> dict:
    sample = {"seconds": 1.0, "peak_rss_bytes": 500 * 1024**2}
    query = {"query": "radar", "source": "radartutorial", "mode": "hybrid"}
    return {
        "target": "windows-amd64",
        "native_target": "windows-amd64",
        "hardware": {"cpu": "reference", "os": "Windows"},
        "content_sha256": "source",
        "text_query": query,
        "dataset_id": "dataset",
        "acceptance": {"dataset_id": "dataset"},
        "verification": {
            "ok": True,
            "dataset_id": "dataset",
            "probes": 4,
            "image_probes": 3,
            "observation_probes": 3,
        },
        "binary": {"bytes": 200 * 1024**2},
        "archive": {"bytes": 190 * 1024**2},
        "preview_bytes": 20 * 1024**2,
        "modes": {
            mode: {
                "budget": mode if mode in {"image", "image_text"} else "text",
                "samples": [deepcopy(sample) for _ in range(21)],
                "repeated": {"p95": 1.0},
                "stable": True,
                "args": query_commands(query, "<query image>")[mode],
            }
            for mode in MODES
        },
    }


def test_resource_gates_cover_every_mode_and_missing_peak_memory() -> None:
    contract = json.loads(CONTRACT.read_bytes())
    report = candidate()
    assert all(resource_checks(report, contract).values())
    report["modes"]["text"]["samples"][0]["peak_rss_bytes"] = None
    assert not resource_checks(report, contract)["memory"]
    report["modes"].pop("observations_text")
    assert not resource_checks(report, contract)["sample_count"]


def test_resource_gates_reject_short_runs_cross_targets_and_oversized_archives() -> (
    None
):
    contract = json.loads(CONTRACT.read_bytes())
    report = candidate()
    report["native_target"] = "linux-amd64"
    report["modes"]["image"]["samples"].pop()
    report["archive"]["bytes"] = 257 * 1024**2
    checks = resource_checks(report, contract)
    assert not checks["native_target"]
    assert not checks["sample_count"]
    assert not checks["archive_size"]


def test_reference_comparison_rejects_different_hardware_and_observation_regression() -> (
    None
):
    contract = json.loads(CONTRACT.read_bytes())
    report = candidate()
    baseline = {
        "hardware": deepcopy(report["hardware"]),
        "dataset": {
            "content_sha256": "source",
            "dataset_id": contract["baseline"]["dataset_id"],
        },
        "latency": {
            "probe_query": report["text_query"],
            "repeat_seconds": {"p95": 1.0},
        },
        "peak_rss_bytes": 500 * 1024**2,
    }
    assert all(reference_checks(report, baseline, contract).values())
    for sample in report["modes"]["observations_text"]["samples"]:
        sample["seconds"] = 1.3
    assert not reference_checks(report, baseline, contract)["reference_latency"]
    baseline["hardware"]["cpu"] = "other"
    assert not any(reference_checks(report, baseline, contract).values())


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows-amd64"),
        ("Darwin", "arm64", "darwin-arm64"),
        ("Linux", "aarch64", "linux-arm64"),
    ],
)
def test_native_architecture_names(
    system: str, machine: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("measure_release.platform.system", lambda: system)
    monkeypatch.setattr("measure_release.platform.machine", lambda: machine)
    assert native_target() == expected


def test_functional_candidate_checks_preserve_full_release_gates(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = "windows-amd64"
    binary = tmp_path / "pipelines-windows-amd64.exe"
    binary.write_bytes(b"candidate")
    bundle = tmp_path / "dataset.zip"
    with zipfile.ZipFile(bundle, "w") as archive:
        archive.writestr(
            "manifest.json", json.dumps({"dataset_id": "data", "image": {}})
        )
    verified = {"ok": True, "dataset_id": "data", "probes": 3, "image_probes": 3}
    monkeypatch.setattr("measure_release.native_target", lambda: target)
    monkeypatch.setattr("measure_release.measure", lambda _: (verified, {}))
    monkeypatch.setattr(
        "measure_release.accept", lambda *_: print(json.dumps({"dataset_id": "data"}))
    )
    report = functional_acceptance(binary, bundle, target)
    assert report["functional_checks_passed"] is True
    assert report["kind"] == "functional"
    assert "resource_gates_passed" not in report
    assert "release_quality_established" not in report
    verified["image_probes"] = 0
    assert (
        functional_acceptance(binary, bundle, target)["functional_checks_passed"]
        is False
    )
    verified["dataset_id"] = "wrong"
    assert functional_acceptance(binary, bundle, target)["checks"][0]["passed"] is False
    with pytest.raises(ValueError, match="native target"):
        functional_acceptance(binary, bundle, "linux-amd64")
