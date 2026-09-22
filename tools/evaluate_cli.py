"""Run source-owned retrieval cases against an actual offline CLI binary."""

import argparse
import json
import subprocess
import time
from collections.abc import Callable
from pathlib import Path


def search_args(case: dict) -> list[str]:
    args = [
        "search",
        "--source",
        case["source"],
        "--mode",
        case["mode"],
        "--limit",
        "20",
    ]
    if case.get("kind"):
        args.extend(["--kind", case["kind"]])
    return [*args, case["query"]]


def evaluate(
    binary: Path, cases: Path, *, runner: Callable[..., dict] | None = None
) -> dict:
    def run(*args: str) -> dict:
        return json.loads(
            subprocess.run(
                [str(binary.resolve()), *args],
                capture_output=True,
                check=True,
                timeout=120,
            ).stdout
        )

    execute = runner or run
    info = execute("info")
    results = []
    skipped = []
    for case in json.loads(cases.read_text(encoding="utf-8")):
        if case["source"] not in info["sources"]:
            skipped.append(case)
            continue
        start = time.perf_counter()
        response = execute(*search_args(case))
        items = response["results"]
        if any(item["source"] != case["source"] for item in items):
            raise ValueError("Source filter leaked an unrelated entity")
        if case.get("kind") and any(item["kind"] != case["kind"] for item in items):
            raise ValueError("Kind filter leaked an unrelated entity")
        rank = next(
            (i + 1 for i, item in enumerate(items) if item["id"] == case["expected"]),
            None,
        )
        results.append(
            {
                **case,
                "rank": rank,
                "first": items[0]["id"] if items else None,
                "passed": rank is not None and rank <= case["max_rank"],
                "seconds": round(time.perf_counter() - start, 3),
            }
        )
    return {
        "dataset_id": info["dataset_id"],
        "results": results,
        "skipped": skipped,
        "ok": bool(results)
        and all(row["passed"] for row in results if row.get("required", True)),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("binary", type=Path)
    parser.add_argument(
        "--cases",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "tests/fixtures/retrieval.json",
    )
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = evaluate(args.binary, args.cases)
    text = json.dumps(report, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
