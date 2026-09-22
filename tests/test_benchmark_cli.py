import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import benchmark_cli
import pytest
from benchmark_cli import benchmark, distribution, measure, summarize
from evaluate_cli import evaluate


def test_quality_keeps_exploratory_failures_and_reports_missing_ranks() -> None:
    rows: list[dict] = [
        {"source": "a", "mode": "hybrid", "rank": 1, "passed": True},
        {"source": "b", "mode": "vector", "rank": 4, "passed": True, "required": False},
        {
            "source": "b",
            "mode": "vector",
            "rank": None,
            "passed": False,
            "required": False,
        },
    ]
    summary = summarize(rows)
    assert summary["by_cohort"]["required"]["top1"] == 1
    exploratory = summary["by_cohort"]["exploratory"]
    assert exploratory["count"] == 2
    assert exploratory["recall_at_5"] == 0.5
    assert exploratory["mrr_at_20"] == 0.125
    assert summary["by_source"]["b"] == exploratory
    assert "vector_description_or_specification" in summary["by_task"]
    assert distribution(list(range(1, 21)))["p95"] == 19


def test_measure_collects_json_memory_and_propagates_process_errors() -> None:
    response, sample = measure([sys.executable, "-c", "print('{\"ok\": true}')"])
    assert response == {"ok": True}
    assert sample["seconds"] > 0
    if sample["peak_rss_bytes"] is not None:
        assert sample["peak_rss_bytes"] > 0
    with pytest.raises(subprocess.CalledProcessError) as error:
        measure([sys.executable, "-c", "import sys; sys.exit(3)"])
    assert error.value.returncode == 3
    with pytest.raises(subprocess.TimeoutExpired):
        measure([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.05)


def test_macos_measure_uses_the_exact_child_peak_in_bytes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Child:
        pid = 123
        returncode = None

        def __init__(self, *args: object, **kwargs: object) -> None:
            kwargs["stdout"].write(b'{"ok":true}')  # type: ignore[attr-defined]

        def __enter__(self) -> "Child":
            return self

        def __exit__(self, *args: object) -> None:
            assert self.returncode == 0

        def poll(self) -> int | None:
            return self.returncode

    calls = []

    def wait4(pid: int, flags: int) -> tuple[int, int, SimpleNamespace]:
        calls.append((pid, flags))
        return pid, 0, SimpleNamespace(ru_maxrss=123456789)

    monkeypatch.setattr(benchmark_cli.sys, "platform", "darwin")
    monkeypatch.setattr(benchmark_cli.subprocess, "Popen", Child)
    monkeypatch.setattr(benchmark_cli.os, "wait4", wait4, raising=False)
    monkeypatch.setattr(benchmark_cli.os, "WNOHANG", 1, raising=False)
    monkeypatch.setattr(benchmark_cli.os, "waitstatus_to_exitcode", lambda code: code)
    response, sample = measure(["binary"])
    assert response == {"ok": True}
    assert calls == [(123, 1)]
    assert sample["peak_rss_bytes"] == 123456789
    assert sample["memory_method"] == "darwin_wait4_maxrss"


def test_evaluator_preserves_filters_and_required_gate(tmp_path: Path) -> None:
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "source": "a",
                    "kind": "radar",
                    "query": "x",
                    "mode": "hybrid",
                    "expected": "a:1",
                    "max_rank": 1,
                },
                {
                    "source": "a",
                    "query": "y",
                    "mode": "vector",
                    "expected": "a:2",
                    "max_rank": 5,
                    "required": False,
                },
            ]
        )
    )
    commands = []

    def run(*args: str) -> dict:
        commands.append(args)
        if args == ("info",):
            return {"dataset_id": "data", "sources": ["a"]}
        return {"results": [{"id": "a:1", "source": "a", "kind": "radar"}]}

    report = evaluate(Path("unused"), cases, runner=run)
    assert report["ok"]
    assert report["results"][1]["rank"] is None
    assert commands[1] == (
        "search",
        "--source",
        "a",
        "--mode",
        "hybrid",
        "--limit",
        "20",
        "--kind",
        "radar",
        "x",
    )

    def leaked(*args: str) -> dict:
        if args == ("info",):
            return run(*args)
        return {"results": [{"id": "b:1", "source": "b", "kind": "radar"}]}

    with pytest.raises(ValueError, match="Source filter"):
        evaluate(Path("unused"), cases, runner=leaked)


def test_benchmark_first_search_precedes_info_and_flags_skipped_sources(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_path / "binary"
    binary.write_bytes(b"binary")
    (tmp_path / "binary.zip").write_bytes(b"archive")
    cases = tmp_path / "cases.json"
    cases.write_text(
        json.dumps(
            [
                {
                    "source": "a",
                    "query": "x",
                    "mode": "hybrid",
                    "expected": "a:1",
                    "max_rank": 1,
                },
                {
                    "source": "missing",
                    "query": "y",
                    "mode": "hybrid",
                    "expected": "missing:1",
                    "max_rank": 1,
                },
            ]
        )
    )
    commands = []

    def fake(command: list[str]) -> tuple[dict, dict]:
        commands.append(command[1:])
        response = (
            {"dataset_id": "data", "sources": ["a"]}
            if command[1] == "info"
            else {"results": [{"id": "a:1", "source": "a"}]}
        )
        return response, {
            "seconds": 0.1,
            "peak_rss_bytes": None,
            "memory_method": "unavailable",
        }

    monkeypatch.setattr("benchmark_cli.measure", fake)
    report = benchmark(binary, cases, tmp_path, 2)
    assert [command[0] for command in commands] == [
        "search",
        "search",
        "search",
        "info",
        "search",
    ]
    assert report["evaluation"]["ok"]
    assert not report["ok"]
    assert report["peak_rss_bytes"] is None
    assert report["archives"][0]["bytes"] == 7
    assert len(report["binary"]["sha256"]) == 64
