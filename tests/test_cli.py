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
            "--root",
            str(root),
            "status",
            "radartutorial",
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
        [sys.executable, "-m", "pipelines", "--root", str(root), "build", "unknown"],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert result.returncode != 0
    assert "Unknown source: unknown" in result.stderr
    assert not root.exists()


def test_reader_import_needs_only_the_standard_library() -> None:
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [
            sys.executable,
            "-S",
            "-c",
            "import sys; sys.path.insert(0, sys.argv[1]); import pipelines; "
            "assert not {'scrapy', 'bs4', 'markdownify', 'filelock'} & sys.modules.keys(); "
            "print(pipelines.Dataset.__name__)",
            str(source),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=15,
    )
    assert result.stdout.strip() == "Dataset"
