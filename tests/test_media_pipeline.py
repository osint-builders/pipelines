import io
import json
import socket
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from PIL import Image
from test_pipeline import HTML, URL, SmallSource

from pipelines.__main__ import main
from pipelines.archive import Archive
from pipelines.audit import audit
from pipelines.build import build, publish
from pipelines.distribution import collect_artifacts
from pipelines.media import MediaCandidate, MediaReference, MediaStore, media_id
from pipelines.media_pipeline import media, media_coverage
from pipelines.snapshot import load_snapshot, status
from pipelines.sources.base import Source

IMAGE_URL = "https://images.example/radar.png"


def test_coverage_counts_original_groups_and_missing_entities() -> None:
    entities = [{"id": "fixture:a"}, {"id": "fixture:b"}, {"id": "fixture:c"}]
    references = [{"entity_id": entity["id"]} for entity in entities[:2]]
    report = {
        "records": [
            {
                "url": url,
                "state": state,
                "references": references,
                "occurrences": [
                    {
                        "original_url": IMAGE_URL,
                        "associated": True,
                        "exclusion_reason": "",
                    }
                ],
            }
            for url, state in [
                (IMAGE_URL, "saved"),
                (IMAGE_URL + "?small", "saved"),
                (IMAGE_URL + "?missing", "failed"),
            ]
        ]
    }
    coverage = media_coverage(report, entities)
    assert coverage["with_saved_media"] == 2
    assert coverage["without_candidates"] == ["fixture:c"]
    assert coverage["multiple_subject_records"] == 3
    assert coverage["by_entity"][0]["saved_original_groups"] == 1
    assert coverage["by_entity"][0]["saved"] == 2
    assert coverage["by_entity"][0]["failed"] == 1
    assert coverage["by_entity"][0]["outcome"] == "incomplete"
    assert coverage["by_entity"][2]["outcome"] == "no_applicable_images"
    assert coverage["by_entity"][2]["reason"] == "no_candidates"
    assert coverage["outcomes"] == {"incomplete": 2, "no_applicable_images": 1}


def test_coverage_excludes_unrelated_occurrences_of_a_saved_url() -> None:
    report: dict = {
        "records": [
            {
                "url": IMAGE_URL,
                "state": "saved",
                "references": [{"entity_id": "fixture:a"}, {"entity_id": "fixture:b"}],
                "occurrences": [
                    {
                        "original_url": IMAGE_URL,
                        "associated": True,
                        "exclusion_reason": "",
                        "references": [{"entity_id": "fixture:a"}],
                    },
                    {
                        "original_url": "https://example.org/unrelated.png",
                        "associated": True,
                        "exclusion_reason": "unrelated",
                        "references": [{"entity_id": "fixture:b"}],
                    },
                ],
            }
        ]
    }
    coverage = media_coverage(report, [{"id": "fixture:a"}, {"id": "fixture:b"}])
    assert coverage["with_saved_media"] == 1
    assert coverage["by_entity"][0]["saved_original_groups"] == 1
    assert coverage["by_entity"][1]["saved"] == 0
    assert coverage["by_entity"][1]["excluded"] == 1
    assert coverage["by_entity"][1]["saved_original_groups"] == 0
    assert coverage["by_entity"][0]["outcome"] == "saved"
    assert coverage["by_entity"][1]["outcome"] == "no_applicable_images"
    assert coverage["by_entity"][1]["reason"] == "no_eligible_candidates"
    report["records"][0]["occurrences"].pop()
    coverage = media_coverage(report, [{"id": "fixture:a"}, {"id": "fixture:b"}])
    assert coverage["by_entity"][1]["saved"] == 0
    assert coverage["by_entity"][1]["excluded"] == 1


def test_media_quality_reports_capture_limits_without_counting_excluded_captions() -> (
    None
):
    reference = {"entity_id": "fixture:a"}
    report = {
        "records": [
            {
                "url": IMAGE_URL,
                "state": "saved",
                "references": [reference],
                "width": 320,
                "height": 180,
                "sha256": "a" * 64,
                "occurrences": [
                    {
                        "original_url": IMAGE_URL,
                        "associated": True,
                        "exclusion_reason": "",
                        "role": "preview",
                        "caption": "",
                    },
                    {
                        "original_url": IMAGE_URL,
                        "associated": False,
                        "exclusion_reason": "navigation_image",
                        "role": "original",
                        "caption": "Unrelated branding",
                    },
                ],
            },
            {
                "url": IMAGE_URL + "?original",
                "state": "failed",
                "error": "http_404",
                "references": [reference],
            },
        ],
        "exact_duplicates": [],
        "near_duplicates": [{"left": "a" * 64, "right": "b" * 64}],
    }
    coverage = media_coverage(report, [{"id": "fixture:a"}])
    assert coverage["outcomes"] == {"incomplete": 1}
    quality = coverage["quality"]
    assert quality["saved_records_by_role"] == {"preview": 1}
    assert quality["captioned_saved_records"] == 0
    assert quality["unique_contents"] == 1
    assert quality["resolution"] == {
        "measured_records": 1,
        "min_width": 320,
        "max_width": 320,
        "min_height": 180,
        "max_height": 180,
        "short_edge_below_224": 1,
    }
    assert quality["failed_records_by_reason"] == {"http_404": 1}
    assert quality["excluded_occurrences_by_reason"] == {"navigation_image": 1}
    assert quality["near_duplicate_pairs"] == 1


class ImageSource(SmallSource):
    media_origins = ("https://images.example",)
    invalid_reference = False

    def discover_media(
        self, url: str, body: bytes, entities: list[dict]
    ) -> list[MediaCandidate]:
        assert url == URL and body == HTML
        reference = MediaReference(
            "radartutorial:missing" if self.invalid_reference else entities[0]["id"],
            entities[0]["evidence"][0]["id"],
            "Front view",
        )
        return [
            MediaCandidate(IMAGE_URL, [reference]),
            MediaCandidate("https://images.example/logo.png", [], "navigation"),
            MediaCandidate("https://images.example/orphan.png", []),
        ]


@pytest.fixture
def published(tmp_path: Path) -> tuple[ImageSource, Path]:
    source = ImageSource()
    source_dir = tmp_path / source.id
    with closing(Archive(source_dir / "archives" / "fixture")) as archive:
        archive.add(URL)
        archive.save(URL, 200, HTML, "text/html", {})
        archive.mark_complete(source.id)
        snapshot = publish(source, archive, source_dir)
    return source, snapshot


def no_network(*args: object, **kwargs: object) -> None:
    raise AssertionError("Offline media operation attempted network access")


def save_image(source: ImageSource, root: Path, archive_id: str = "fixture") -> dict:
    stream = io.BytesIO()
    Image.new("RGB", (12, 8), (80, 140, 200)).save(stream, format="PNG")
    image = root / "download.png"
    image.write_bytes(stream.getvalue())
    with MediaStore(root) as store:
        return store.save(
            source.id,
            archive_id,
            media_id(source.id, IMAGE_URL),
            image,
            content_type="image/png",
        )


def test_unsupported_adapter_creates_no_media_or_snapshot(tmp_path: Path) -> None:
    text_only = cast(Source, SimpleNamespace(id="radartutorial"))
    assert media(text_only, tmp_path, download=True) == {
        "source": "radartutorial",
        "supported": False,
        "reason": "no_media_adapter",
    }
    assert list(tmp_path.iterdir()) == []


def test_discovery_is_offline_repeatable_and_keeps_all_outcomes(
    published: tuple[ImageSource, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, snapshot = published
    before = (snapshot / "entities.jsonl").read_bytes()
    monkeypatch.setattr(socket, "socket", no_network)
    report = media(source, tmp_path)
    assert report["counts"] == {
        "discovered": 3,
        "pending": 1,
        "saved": 0,
        "failed": 0,
        "excluded": 1,
        "unassociated": 1,
    }
    assert media(source, tmp_path)["records"] == report["records"]
    assert (snapshot / "entities.jsonl").read_bytes() == before
    assert not (snapshot / "media.json").exists()
    assert status(tmp_path / source.id)["media"]["complete"] is False
    assert audit(source, tmp_path)["ok"] is False


def test_bad_associations_fail_before_registration(
    published: tuple[ImageSource, Path], tmp_path: Path
) -> None:
    source, _ = published
    source.invalid_reference = True
    with pytest.raises(ValueError, match="belong to its archived page"):
        media(source, tmp_path)
    assert not (tmp_path / "media").exists()


def test_offline_extraction_replays_media_without_changing_text_artifacts(
    published: tuple[ImageSource, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, previous = published
    before = collect_artifacts(tmp_path, [source.id])
    media(source, tmp_path)
    record = save_image(source, tmp_path)
    monkeypatch.setattr(socket, "socket", no_network)
    snapshot = build(source, tmp_path, archive_id="fixture")
    report = json.loads((snapshot / "media.json").read_text(encoding="utf-8"))
    saved = [row for row in report["records"] if row["state"] == "saved"]
    assert [row["sha256"] for row in saved] == [record["sha256"]]
    assert saved[0]["references"][0]["caption"] == "Front view"
    assert load_snapshot(tmp_path / source.id)[0]["schema_version"] == 2
    assert collect_artifacts(tmp_path, [source.id]) == before
    assert (snapshot / "entities.jsonl").read_bytes() == (
        previous / "entities.jsonl"
    ).read_bytes()
    assert not (previous / "media.json").exists()
    assert status(tmp_path / source.id)["media"]["complete"] is True
    assert audit(source, tmp_path)["ok"] is True


def test_failed_capture_keeps_text_and_checkpoints_for_resume(
    published: tuple[ImageSource, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, snapshot = published
    pointer = (snapshot.parent.parent / "current.json").read_bytes()

    def fail_capture(source: SmallSource, root: Path, archive_id: str) -> dict:
        with MediaStore(root) as store:
            store.mark(
                source.id,
                archive_id,
                media_id(source.id, IMAGE_URL),
                "failed",
                "timeout",
            )
        raise OSError("Interrupted transfer")

    monkeypatch.setattr("pipelines.media_download.capture_media", fail_capture)
    with pytest.raises(OSError, match="Interrupted"):
        media(source, tmp_path, download=True)
    checkpoint = tmp_path / source.id / "archives/fixture/media.json"
    report = json.loads(checkpoint.read_text(encoding="utf-8"))
    assert report["counts"]["failed"] == 1
    assert report["complete"] is False
    assert (snapshot.parent.parent / "current.json").read_bytes() == pointer
    assert audit(source, tmp_path)["media"]["complete"] is False


def test_cli_discovery_and_explicit_download_use_the_shared_workflow(
    published: tuple[ImageSource, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, _ = published
    calls = []

    def capture(source: ImageSource, root: Path, archive_id: str) -> dict:
        calls.append(archive_id)
        return save_image(source, root, archive_id)

    monkeypatch.setattr("pipelines.registry.get_source", lambda name: source)
    monkeypatch.setattr("pipelines.media_download.capture_media", capture)
    args = ["pipeline-build", "media", source.id, "--root", str(tmp_path)]
    monkeypatch.setattr(sys, "argv", args)
    main()
    assert json.loads(capsys.readouterr().out)["counts"]["pending"] == 1
    assert not calls
    output = tmp_path / "report.json"
    monkeypatch.setattr(sys, "argv", [*args, "--download", "--output", str(output)])
    main()
    report = json.loads(capsys.readouterr().out)
    assert json.loads(output.read_text(encoding="utf-8")) == report
    assert report["complete"] is True and report["counts"]["saved"] == 1
    assert calls == ["fixture"]


def test_media_manifest_loss_cannot_pass_audit(
    published: tuple[ImageSource, Path], tmp_path: Path
) -> None:
    source, _ = published
    media(source, tmp_path)
    save_image(source, tmp_path)
    build(source, tmp_path, archive_id="fixture")
    (tmp_path / "media/manifest.sqlite").unlink()
    with pytest.raises(ValueError, match="archive is missing"):
        audit(source, tmp_path)


def test_published_media_capture_cannot_be_silently_replaced(
    published: tuple[ImageSource, Path], tmp_path: Path
) -> None:
    source, _ = published
    media(source, tmp_path)
    save_image(source, tmp_path)
    snapshot = build(source, tmp_path, archive_id="fixture")
    path = snapshot / "media.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    saved = next(row for row in report["records"] if row["state"] == "saved")
    saved["sha256"] = "a" * 64
    path.write_text(json.dumps(report), encoding="utf-8")
    with pytest.raises(ValueError, match="differs from its archived capture"):
        audit(source, tmp_path)


def test_incomplete_media_makes_cli_audit_fail_with_a_json_report(
    published: tuple[ImageSource, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source, _ = published
    media(source, tmp_path)
    monkeypatch.setattr("pipelines.registry.get_source", lambda name: source)
    monkeypatch.setattr(
        sys, "argv", ["pipeline-build", "audit", source.id, "--root", str(tmp_path)]
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False and report["media"]["counts"]["pending"] == 1
    monkeypatch.setattr("pipelines.media_download.capture_media", lambda *args: {})
    monkeypatch.setattr(
        sys,
        "argv",
        ["pipeline-build", "media", source.id, "--root", str(tmp_path), "--download"],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 1
    report = json.loads(capsys.readouterr().out)
    assert report["complete"] is False and report["counts"]["pending"] == 1
