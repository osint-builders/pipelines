import json
import re
from pathlib import Path
from urllib.parse import urlsplit

from pipelines.media import media_id


def test_pilot_inventory_preserves_seed_assignments_and_provenance() -> None:
    directory = Path(__file__).parent / "fixtures"
    pilot = json.loads((directory / "media_pilot.json").read_text(encoding="utf-8"))
    seed = json.loads((directory / "multimodal.json").read_text(encoding="utf-8"))
    originals = {
        urlsplit(row["url"])[:3]: row for row in seed["media"] if "crop_from" not in row
    }
    identities = set()
    for row in pilot["records"]:
        source = row["id"].split(":", 1)[0]
        assert row["id"] == media_id(source, row["url"])
        assert row["id"] not in identities
        identities.add(row["id"])
        assert re.fullmatch(r"[0-9a-f]{64}", row["sha256"])
        assert row["entity_ids"] and row["evidence_ids"]
        assert all(identity.startswith(source + ":") for identity in row["entity_ids"])
        assert row["visual_observation"] and row["association_limitation"]
        original = originals.get(urlsplit(row["url"])[:3])
        if original:
            assert row["seed_media_id"] == original["id"]
            assert row["benchmark_split"] == original["split"]
        else:
            assert row["benchmark_split"] == "unassigned"
    assert {row["id"].split(":", 1)[0] for row in pilot["records"]} == {
        "commons",
        "militaryperiscope",
    }
