"""Package and validate evidence-backed claims and reviewed relationships."""

import json
import zipfile

from pipelines.distribution import canonical, sha256
from pipelines.research import FIELD_CATALOGS, VERSION, build_research

CLAIMS_MEMBER = "research/claims.json"
RELATIONS_MEMBER = "research/relations.json"
MEMBERS = {CLAIMS_MEMBER, RELATIONS_MEMBER}
MAX_MEMBER_BYTES = 32 * 1024 * 1024


def build_research_members(
    entities: list[dict], *, version: str = VERSION
) -> tuple[dict[str, bytes], dict]:
    claims, relations = build_research(entities)
    catalog = FIELD_CATALOGS[version]
    if any(
        claim["field"] not in {field["name"] for field in catalog} for claim in claims
    ):
        raise ValueError("Claim is outside the research field catalog")
    if len(claims) > 100_000 or len(relations) > 20_000:
        raise ValueError("Research record limit exceeded")
    members = {CLAIMS_MEMBER: canonical(claims), RELATIONS_MEMBER: canonical(relations)}
    if any(len(body) > MAX_MEMBER_BYTES for body in members.values()):
        raise ValueError("Research member exceeds size limit")
    metadata = {
        "version": version,
        "claims": len(claims),
        "relations": len(relations),
        "fields": catalog,
    }
    return members, metadata


def validate_research_bundle(
    archive: zipfile.ZipFile, manifest: dict, entities: list[dict] | None = None
) -> dict | None:
    present = {name for name in archive.namelist() if name.startswith("research/")}
    declared = {
        name for name in manifest.get("files", {}) if name.startswith("research/")
    }
    if "research" not in manifest:
        if present or declared:
            raise ValueError("Research member without manifest extension")
        return None
    metadata = manifest["research"]
    if (
        not isinstance(metadata, dict)
        or set(metadata) != {"version", "claims", "relations", "fields"}
        or not isinstance(metadata["version"], str)
        or metadata["version"] not in FIELD_CATALOGS
        or canonical(metadata["fields"])
        != canonical(FIELD_CATALOGS[metadata["version"]])
        or type(metadata["claims"]) is not int
        or type(metadata["relations"]) is not int
        or not 0 <= metadata["claims"] <= 100_000
        or not 0 <= metadata["relations"] <= 20_000
        or present != MEMBERS
        or declared != MEMBERS
    ):
        raise ValueError("Invalid research manifest")
    for name in MEMBERS:
        if (
            archive.namelist().count(name) != 1
            or archive.getinfo(name).file_size > MAX_MEMBER_BYTES
        ):
            raise ValueError("Invalid research member")
        if sha256(archive.read(name)) != manifest["files"][name]:
            raise ValueError("Research member checksum mismatch")
    if entities is None:
        entities = []
        for entry in json.loads(archive.read("index.json")):
            name = "entities/" + entry["id"].replace(":", "/") + ".json"
            raw = archive.read(name)
            if sha256(raw) != manifest["files"].get(name):
                raise ValueError("Research entity checksum mismatch")
            entity = json.loads(raw)
            if entity["id"] != entry["id"] or entity["source"] != entry["source"]:
                raise ValueError("Research entity identity mismatch")
            entities.append(entity)
    expected, expected_metadata = build_research_members(
        entities, version=metadata["version"]
    )
    if metadata != expected_metadata or any(
        archive.read(name) != body for name, body in expected.items()
    ):
        raise ValueError(
            "Research sidecars do not reproduce retained source evidence and rules"
        )
    return metadata
