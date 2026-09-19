import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pipelines", description="Build reference snapshots or query them offline"
    )
    parser.add_argument(
        "--root", type=Path, required=True, help="Dataset storage directory"
    )
    subcommands = parser.add_subparsers(dest="command", required=True)
    build_parser = subcommands.add_parser("build")
    build_parser.add_argument("source")
    reindex_parser = subcommands.add_parser(
        "reindex", help="Rebuild from archived HTML without network access"
    )
    reindex_parser.add_argument("source")
    reindex_parser.add_argument("archive")
    search_parser = subcommands.add_parser("search")
    search_parser.add_argument("source")
    search_parser.add_argument("query")
    search_parser.add_argument("--kind")
    search_parser.add_argument("--category")
    search_parser.add_argument("--limit", type=int, default=20)
    read_parser = subcommands.add_parser("read")
    read_parser.add_argument("source")
    read_parser.add_argument("id")
    status_parser = subcommands.add_parser("status")
    status_parser.add_argument("source")
    args = parser.parse_args()
    if args.command in {"build", "reindex"}:
        from pipelines.build import build
        from pipelines.registry import get_source

        result = build(
            get_source(args.source),
            args.root,
            archive_id=getattr(args, "archive", None),
        )
        print(f"Published {result}")
    elif args.command == "status":
        from pipelines.reader import status

        print(json.dumps(status(args.root / args.source), ensure_ascii=True, indent=2))
    else:
        from pipelines.reader import Dataset

        with Dataset(args.root / args.source / "published") as dataset:
            output: dict | list[dict]
            if args.command == "search":
                output = dataset.search(
                    args.query, kind=args.kind, category=args.category, limit=args.limit
                )
            else:
                output = dataset.read(args.id)
            print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
