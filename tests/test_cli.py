import json
import subprocess
import sys
from pathlib import Path


def test_module_entry_point_reports_missing_dataset_without_creating_it(
    tmp_path: Path,
) -> None:
    root = tmp_path / "data"
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipelines",
            "status",
            "radartutorial",
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert json.loads(result.stdout) == {"published": None, "work": None}
    assert not root.exists()


def test_unknown_source_cannot_create_a_build_directory(tmp_path: Path) -> None:
    root = tmp_path / "data"
    result = subprocess.run(
        [sys.executable, "-m", "pipelines", "crawl", "unknown", "--root", str(root)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Unknown source: unknown" in result.stderr
    assert not root.exists()


def test_producer_only_exposes_entity_build_commands() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pipelines", "--help"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert "pipeline-build" in result.stdout
    assert "crawl,extract,status,audit,model,package" in result.stdout
    for obsolete in ("search", "read", "reindex"):
        failed = subprocess.run(
            [sys.executable, "-m", "pipelines", obsolete], capture_output=True
        )
        assert failed.returncode != 0
