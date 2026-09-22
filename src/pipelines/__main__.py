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
    for name in ("crawl", "extract", "status", "audit", "media"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("--root", type=Path, required=True)
        if name == "extract":
            command.add_argument(
                "archive", help="Saved archive ID; no website requests"
            )
        elif name == "audit":
            command.add_argument("--binary", type=Path, help="Also verify CLI exports")
            command.add_argument("--output", type=Path, help="Save the JSON report")
        elif name == "media":
            command.add_argument(
                "--download", action="store_true", help="Fetch discovered originals"
            )
            command.add_argument("--output", type=Path, help="Save the JSON report")
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
    package.add_argument(
        "--image-model", type=Path, help="Pinned offline image model manifest"
    )
    package.add_argument(
        "--image-selection", type=Path, help="Explicit gallery media ID selection"
    )
    package.add_argument(
        "--observations", type=Path, help="Cached image observation analysis"
    )
    package.add_argument(
        "--calibration", type=Path, help="Frozen development-fitted search decisions"
    )
    observe = commands.add_parser(
        "observe", help="Analyze indexed images from the local archive"
    )
    observe.add_argument("--root", type=Path, required=True)
    observe.add_argument("--bundle", type=Path, required=True)
    observe.add_argument("--ocr-model", type=Path)
    observe.add_argument("--description-model", type=Path)
    observe.add_argument(
        "--device",
        choices=("cpu", "cuda"),
        default="cpu",
        help="Description inference device",
    )
    observe.add_argument(
        "--selection", type=Path, help="Restrict analysis to selected indexed media IDs"
    )
    observe.add_argument("--output", type=Path, required=True)
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
    elif args.command == "audit":
        from pipelines.archive import atomic_json
        from pipelines.audit import audit
        from pipelines.registry import get_source

        output = audit(get_source(args.source), args.root, args.binary)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(args.output, output)
    elif args.command == "media":
        from pipelines.archive import atomic_json
        from pipelines.media_pipeline import media
        from pipelines.registry import get_source

        output = media(get_source(args.source), args.root, download=args.download)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            atomic_json(args.output, output)
    elif args.command == "model":
        from pipelines.distribution import download_model

        download_model(args.output)
        output = {"model": str(args.output)}
    elif args.command == "observe":
        from pipelines.observations import observe as observe_images

        output = observe_images(
            args.root,
            args.bundle,
            args.output,
            ocr_model=args.ocr_model,
            description_model=args.description_model,
            device=args.device,
            selection=args.selection,
        )
    else:
        from pipelines.distribution import package as create_package

        result = create_package(
            args.root.resolve(),
            args.source,
            args.model,
            args.cache,
            args.output,
            image_model=args.image_model,
            image_selection=args.image_selection,
            observations=args.observations,
            calibration=args.calibration,
        )
        output = {
            key: value for key, value in result.items() if key not in {"files", "model"}
        }
    print(json.dumps(output, ensure_ascii=True, indent=2))
    if isinstance(output, dict) and (
        (args.command == "audit" and not output["ok"])
        or (args.command == "observe" and output["states"].get("failed", 0))
        or (
            args.command == "media"
            and args.download
            and output.get("supported")
            and not output["complete"]
        )
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
