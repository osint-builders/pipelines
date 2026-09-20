import argparse
import json
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="pipeline-build",
        description="Produce entity datasets for the offline pipelines CLI",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("sources", help="List installed source adapters")
    for name in ("crawl", "extract", "status"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("--root", type=Path, required=True)
        if name == "extract":
            command.add_argument(
                "archive", help="Saved archive ID; no website requests"
            )
    model = commands.add_parser("model", help="Download the pinned embedding model")
    model.add_argument("--output", type=Path, required=True)
    package = commands.add_parser(
        "package", help="Create a vector bundle from existing entity snapshots"
    )
    package.add_argument("--root", type=Path, required=True)
    package.add_argument("--source", action="append", required=True)
    package.add_argument("--model", type=Path, required=True)
    package.add_argument("--cache", type=Path, required=True)
    package.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    output: dict | list[str]
    if args.command == "sources":
        from pipelines.registry import source_names

        output = source_names()
    elif args.command in {"crawl", "extract"}:
        from pipelines.build import build
        from pipelines.registry import get_source

        output = {
            "snapshot": str(
                build(
                    get_source(args.source),
                    args.root,
                    archive_id=getattr(args, "archive", None),
                )
            )
        }
    elif args.command == "status":
        from pipelines.registry import source_names
        from pipelines.snapshot import status

        if args.source not in source_names():
            parser.error(f"Unknown source: {args.source}")
        output = status(args.root / args.source)
    elif args.command == "model":
        from pipelines.distribution import download_model

        download_model(args.output)
        output = {"model": str(args.output)}
    else:
        from pipelines.distribution import package as create_package

        result = create_package(
            args.root.resolve(), args.source, args.model, args.cache, args.output
        )
        output = {
            key: value for key, value in result.items() if key not in {"files", "model"}
        }
    print(json.dumps(output, ensure_ascii=True, indent=2))


if __name__ == "__main__":
    main()
