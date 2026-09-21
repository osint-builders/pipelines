"""Package source captions and the reproducible text ranking policy."""

import json
import math
import re
import zipfile
from pathlib import Path

from pipelines.distribution import SEARCH_VERSION, canonical, plain_text, sha256
from pipelines.media import media_id
from pipelines.media_context import eligible_reference
from pipelines.media_pipeline import read_media
from pipelines.snapshot import load_snapshot

SEARCH_POLICY = {
    "version": SEARCH_VERSION,
    "k1": 1.2,
    "b": 0.75,
    "rank_constant": 60,
    "lexical_weight": 2.0,
    "semantic_weight": 1.0,
}
CAPTIONS_MEMBER = "search/captions.json"
MAX_CAPTIONS = 100_000
MAX_CAPTION_BYTES = 16_384
MAX_MEMBER_BYTES = 32 * 1024 * 1024


def _policy(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != {*SEARCH_POLICY, "captions"}:
        raise ValueError("Invalid search policy fields")
    if value["version"] != SEARCH_VERSION:
        raise ValueError("Unsupported search policy version")
    for field in ("k1", "b", "lexical_weight", "semantic_weight"):
        number = value[field]
        if (
            type(number) not in {int, float}
            or not math.isfinite(number)
            or not (0 <= number <= 1 if field == "b" else 0 < number <= 10)
            or (field in {"k1", "b"} and number != SEARCH_POLICY[field])
        ):
            raise ValueError("Invalid search policy parameter")
    if (
        type(value["rank_constant"]) is not int
        or not 1 <= value["rank_constant"] <= 1000
        or type(value["captions"]) is not int
        or not 0 <= value["captions"] <= MAX_CAPTIONS
    ):
        raise ValueError("Invalid search policy count or rank constant")
    return value


def _caption(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("Source caption must be text")
    text = plain_text(value)
    if len(text.encode("utf-8")) > MAX_CAPTION_BYTES:
        raise ValueError("Source caption exceeds the text limit")
    return text


def build_search_members(
    root: Path,
    sources: list[str],
    entities: list[dict],
    *,
    allowed_media_ids: set[str] | None = None,
) -> tuple[dict[str, bytes], dict]:
    owners = {
        entity["id"]: (
            position,
            {page["id"]: page["url"] for page in entity["evidence"]},
        )
        for position, entity in enumerate(entities)
    }
    captions: dict[tuple[int, str, str], str] = {}
    known_media: set[str] = set()

    def add(record: dict, reference: dict, value: object) -> None:
        identity, evidence = reference["entity_id"], reference["evidence_id"]
        if (
            identity not in owners
            or evidence not in owners[identity][1]
            or identity.partition(":")[0] != record["source"]
        ):
            raise ValueError("Caption reference is absent from source entity evidence")
        text = _caption(value)
        if text:
            key = (owners[identity][0], evidence, text)
            captions[key] = min(record["id"], captions.get(key, record["id"]))

    for source in sorted(set(sources)):
        snapshot, _ = load_snapshot(root / source)
        source_entities = [row for row in entities if row["source"] == source]
        report = read_media(source, root, snapshot["archive"], entities=source_entities)
        for record in report["records"] if report else []:
            if (
                record["source"] != source
                or record["archive"] != snapshot["archive"]
                or record["id"] != media_id(source, record["url"])
            ):
                raise ValueError("Invalid caption media identity or scope")
            known_media.add(record["id"])
            if allowed_media_ids is not None and record["id"] not in allowed_media_ids:
                continue
            occurrences = record.get("occurrences", [])
            eligible = [
                item
                for item in occurrences
                if item.get("associated") and not item.get("exclusion_reason")
            ]
            for reference in record["references"]:
                if not eligible_reference(record, reference) or (
                    not occurrences and record["state"] in {"excluded", "unassociated"}
                ):
                    continue
                add(record, reference, reference.get("caption", ""))
            for item in eligible:
                for reference in item.get("references", record["references"]):
                    identity, evidence = (
                        reference["entity_id"],
                        reference["evidence_id"],
                    )
                    if item.get("page_url") and (
                        identity not in owners
                        or owners[identity][1].get(evidence) != item["page_url"]
                    ):
                        raise ValueError(
                            "Caption occurrence does not belong to its evidence"
                        )
                    add(record, reference, item.get("caption", ""))
    if allowed_media_ids is not None and not allowed_media_ids.issubset(known_media):
        raise ValueError("Caption selection contains unknown media IDs")
    rows = [
        {"entity": entity, "evidence_id": evidence, "text": text, "media_id": identity}
        for (entity, evidence, text), identity in sorted(captions.items())
    ]
    metadata = _policy({**SEARCH_POLICY, "captions": len(rows)})
    body = canonical(rows)
    if len(body) > MAX_MEMBER_BYTES:
        raise ValueError("Caption index exceeds the member limit")
    return {CAPTIONS_MEMBER: body}, metadata


def _read(archive: zipfile.ZipFile, manifest: dict, name: str) -> bytes:
    if name not in manifest["files"] or archive.namelist().count(name) != 1:
        raise ValueError("Search bundle member is missing, duplicated or undeclared")
    if archive.getinfo(name).file_size > MAX_MEMBER_BYTES:
        raise ValueError("Search bundle member exceeds the size limit")
    body = archive.read(name)
    if sha256(body) != manifest["files"][name]:
        raise ValueError("Search bundle checksum mismatch")
    return body


def _object(pairs: list[tuple[str, object]]) -> dict:
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate search JSON key")
    return result


def _decode(body: bytes) -> object:
    try:
        return json.loads(body, object_pairs_hook=_object)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ValueError("Invalid search bundle JSON") from exc


def validate_search_bundle(
    archive: zipfile.ZipFile, manifest: dict, entities: list[dict] | None = None
) -> dict:
    declared = {name for name in manifest["files"] if name.startswith("search/")}
    actual = [name for name in archive.namelist() if name.startswith("search/")]
    if "search" not in manifest:
        if declared or actual:
            raise ValueError("Search members require a search policy")
        return {}
    if manifest.get("format_version") not in {2, 3, 4}:
        raise ValueError("Search extension requires bundle format 2, 3 or 4")
    metadata = _policy(manifest["search"])
    if declared != {CAPTIONS_MEMBER} or actual != [CAPTIONS_MEMBER]:
        raise ValueError("Search bundle members are missing, duplicated or undeclared")
    rows = _decode(_read(archive, manifest, CAPTIONS_MEMBER))
    if not isinstance(rows, list) or len(rows) != metadata["captions"]:
        raise ValueError("Invalid source caption count")
    index = _decode(_read(archive, manifest, "index.json"))
    if not isinstance(index, list) or len(index) != manifest["entities"]:
        raise ValueError("Invalid caption entity index")
    if entities is not None and [row["id"] for row in entities] != [
        row["id"] for row in index
    ]:
        raise ValueError("Caption entities differ from the bundle index")
    owners: dict[int, set[str]] = {}
    previous: tuple[int, str, str] | None = None
    for row in rows:
        if (
            not isinstance(row, dict)
            or set(row) != {"entity", "evidence_id", "text", "media_id"}
            or type(row["entity"]) is not int
            or not 0 <= row["entity"] < len(index)
            or not isinstance(row["evidence_id"], str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", row["evidence_id"])
            or not isinstance(row["text"], str)
            or not row["text"]
            or _caption(row["text"]) != row["text"]
            or not isinstance(row["media_id"], str)
        ):
            raise ValueError("Invalid source caption record")
        position = row["entity"]
        identity = index[position]["id"]
        source = identity.partition(":")[0]
        if not re.fullmatch(
            re.escape(source) + r":media:[a-f0-9]{24}", row["media_id"]
        ):
            raise ValueError("Invalid source caption media ID")
        key = (position, row["evidence_id"], row["text"])
        if previous is not None and key <= previous:
            raise ValueError("Source captions must be sorted and unique")
        previous = key
        if position not in owners:
            entity = (
                entities[position]
                if entities is not None
                else _decode(
                    _read(
                        archive,
                        manifest,
                        "entities/" + identity.replace(":", "/") + ".json",
                    )
                )
            )
            if not isinstance(entity, dict) or entity.get("id") != identity:
                raise ValueError("Invalid source caption entity")
            owners[position] = {page["id"] for page in entity["evidence"]}
        if row["evidence_id"] not in owners[position]:
            raise ValueError("Caption reference is absent from source entity evidence")
    return metadata
