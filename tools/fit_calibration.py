"""Fit or reproduce acceptance thresholds from reviewed development captures."""

import argparse
from pathlib import Path

from build_cli import verify_bundle

from pipelines.calibration import canonical, digest, fit, parse_artifact, verify_fit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--fixture", type=Path, required=True)
    parser.add_argument("--responses", type=Path, required=True)
    parser.add_argument(
        "--contract", type=Path, default=Path("tests/fixtures/search_acceptance.json")
    )
    destination = parser.add_mutually_exclusive_group(required=True)
    destination.add_argument("--output", type=Path)
    destination.add_argument(
        "--verify", type=Path, help="Recompute this artifact without writing output"
    )
    args = parser.parse_args()
    manifest = verify_bundle(args.bundle)
    fixture = args.fixture.read_bytes()
    responses = args.responses.read_bytes()
    binary_sha256 = digest(args.binary.read_bytes())
    contract = args.contract.read_bytes()
    if args.verify:
        artifact = parse_artifact(args.verify.read_bytes(), manifest)
        verify_fit(
            artifact,
            manifest,
            fixture,
            responses,
            binary_sha256=binary_sha256,
            contract_bytes=contract,
        )
    else:
        artifact = fit(
            manifest,
            fixture,
            responses,
            binary_sha256=binary_sha256,
            contract_bytes=contract,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(canonical(artifact))
    print(
        canonical(
            {
                "artifact_sha256": digest(canonical(artifact)),
                "retrieval_sha256": artifact["retrieval_sha256"],
                "profiles": len(artifact["profiles"]),
                "verified": bool(args.verify),
            }
        ).decode()
    )


if __name__ == "__main__":
    main()
