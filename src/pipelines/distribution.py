import base64
import hashlib
import json
import re
import sqlite3
import sys
import urllib.request
import zipfile
from contextlib import closing
from pathlib import Path

from pipelines.model import response_member
from pipelines.snapshot import load_snapshot

FORMAT_VERSION = 2
SEARCH_VERSION = "minilm-chunks-v1"
LOCK = json.loads(Path(__file__).with_name("model.lock.json").read_text())


def canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":")
    ).encode()


def sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def download_model(destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for name, spec in LOCK["files"].items():
        target = destination / name
        if target.exists() and sha256(target.read_bytes()) == spec["sha256"]:
            continue
        url = f"https://huggingface.co/{LOCK['id']}/resolve/{LOCK['revision']}/{spec['path']}"
        with urllib.request.urlopen(url, timeout=120) as response:
            body = response.read()
        if sha256(body) != spec["sha256"]:
            raise ValueError(f"Model checksum mismatch: {name}")
        temporary = target.with_suffix(".tmp")
        temporary.write_bytes(body)
        temporary.replace(target)


def plain_text(markdown: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", markdown)
    text = re.sub(r"https?://\S+", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return " ".join(re.sub(r"[#*_`|]", " ", text).split())


def collect_artifacts(
    root: Path, sources: list[str]
) -> tuple[list[dict], dict[str, bytes], dict[str, bytes]]:
    entities: list[dict] = []
    html: dict[str, bytes] = {}
    responses: dict[str, bytes] = {}
    provenance: dict[str, tuple] = {}
    for source in sorted(set(sources)):
        if not re.fullmatch(r"[a-z0-9_-]+", source):
            raise ValueError("Invalid source ID")
        source_dir = root / source
        captures: dict[str, tuple[bytes, str]] = {}
        manifest, records = load_snapshot(source_dir)
        archive = source_dir / "archives" / manifest["archive"]
        with closing(
            sqlite3.connect(
                (archive / "manifest.sqlite").resolve().as_uri() + "?mode=ro", uri=True
            )
        ) as db:
            for entity in records:
                for evidence in entity["evidence"]:
                    page = db.execute(
                        "SELECT file, sha256, status, content_type FROM pages WHERE url=?",
                        (evidence["url"],),
                    ).fetchone()
                    if page is None or page[2] != 200:
                        raise ValueError(
                            f"Missing successful archive response: {evidence['url']}"
                        )
                    key = f"{source}/{evidence['id']}"
                    capture = (
                        evidence.get("html_origin"),
                        evidence.get("source_response"),
                    )
                    if key in provenance and provenance[key] != capture:
                        raise ValueError("Conflicting provenance for shared evidence")
                    provenance[key] = capture
                    if key not in html:
                        path = (archive / page[0]).resolve()
                        if not path.is_relative_to(archive.resolve()):
                            raise ValueError("Archive path escapes its directory")
                        if evidence["url"] not in captures:
                            raw = path.read_bytes()
                            captures[evidence["url"]] = (raw, sha256(raw))
                        response_body, response_hash = captures[evidence["url"]]
                        if evidence.get("html_origin") in {
                            "api-rendered",
                            "record-rendered",
                        }:
                            response = evidence.get("source_response", {})
                            if (
                                response.get("url") != evidence["url"]
                                or response.get("content_type") != page[3]
                                or response.get("sha256") != page[1]
                                or response_hash != page[1]
                            ):
                                raise ValueError(
                                    "API response checksum or provenance mismatch"
                                )
                            if evidence["html_origin"] == "record-rendered":
                                member = response_member(
                                    source, evidence["url"], page[3]
                                )
                                if (
                                    not evidence.get("record_id")
                                    or not evidence.get("records")
                                    or response.get("body_member") != member
                                    or "body_base64" in response
                                ):
                                    raise ValueError(
                                        "Invalid record response provenance"
                                    )
                                responses[member] = response_body
                            elif (
                                base64.b64decode(
                                    response.get("body_base64", ""), validate=True
                                )
                                != response_body
                                or "body_member" in response
                            ):
                                raise ValueError(
                                    "API response checksum or provenance mismatch"
                                )
                            rendered = (
                                source_dir
                                / "published"
                                / "snapshots"
                                / manifest["snapshot"]
                                / "html"
                                / f"{evidence['id']}.html"
                            )
                            html[key] = rendered.read_bytes()
                        else:
                            if (
                                "source_response" in evidence
                                or "html_origin" in evidence
                                or response_hash != page[1]
                            ):
                                raise ValueError(
                                    "Archive checksum or provenance mismatch"
                                )
                            html[key] = response_body
                    if sha256(html[key]) != evidence["html_sha256"]:
                        raise ValueError(f"HTML checksum mismatch: {evidence['url']}")
                entities.append(entity)
    entities.sort(key=lambda entity: entity["id"])
    if not entities or len({entity["id"] for entity in entities}) != len(entities):
        raise ValueError("Dataset is empty or contains duplicate entity IDs")
    return entities, html, responses


def stable_content(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: stable_content(item)
            for key, item in value.items()
            if key not in {"retrieved_at", "html_sha256", "source_response"}
        }
    if isinstance(value, list):
        return [stable_content(item) for item in value]
    return value


def content_digest(entities: list[dict]) -> str:
    return sha256(canonical(stable_content(entities)))


class Encoder:
    def __init__(self, directory: Path) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        for name, spec in LOCK["files"].items():
            if sha256((directory / name).read_bytes()) != spec["sha256"]:
                raise ValueError(f"Model checksum mismatch: {name}")
        self.tokenizer = Tokenizer.from_file(str(directory / "tokenizer.json"))
        self.tokenizer.no_truncation()
        self.tokenizer.enable_padding()
        options = ort.SessionOptions()
        options.intra_op_num_threads = 4
        options.inter_op_num_threads = 1
        self.session = ort.InferenceSession(
            str(directory / "model.onnx"),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )

    def chunks(self, entity: dict) -> list[dict]:
        prefix = " | ".join([entity["title"], *entity["aliases"], entity["kind"]])
        prefix_encoding = self.tokenizer.encode(prefix, add_special_tokens=False)
        if len(prefix_encoding.ids) > 48:
            prefix = prefix[: prefix_encoding.offsets[47][1]]
        chunks = [{"text": prefix, "evidence_id": ""}]
        for page in entity["evidence"]:
            text = plain_text(page.get("search_text") or page["markdown"])
            encoded = self.tokenizer.encode(text, add_special_tokens=False)
            for start in range(0, len(encoded.ids), 144):
                end = min(start + 192, len(encoded.ids))
                piece = text[encoded.offsets[start][0] : encoded.offsets[end - 1][1]]
                chunks.append(
                    {"text": prefix + "\n" + piece, "evidence_id": page["id"]}
                )
                if end == len(encoded.ids):
                    break
        for chunk in chunks:
            if len(self.tokenizer.encode(chunk["text"]).ids) > LOCK["max_tokens"]:
                raise ValueError(f"Chunk exceeds model token limit: {entity['id']}")
        return chunks

    def encode(self, texts: list[str]) -> bytes:
        import numpy as np

        tokens = self.tokenizer.encode_batch(texts)
        ids = np.asarray([item.ids for item in tokens], dtype=np.int64)
        mask = np.asarray([item.attention_mask for item in tokens], dtype=np.int64)
        if ids.shape[1] > LOCK["max_tokens"]:
            raise ValueError("Embedding input exceeds 256 tokens")
        output = self.session.run(
            None,
            {
                "input_ids": ids,
                "attention_mask": mask,
                "token_type_ids": np.zeros_like(ids),
            },
        )[0]
        weights = mask[:, :, None].astype(np.float32)
        vectors = (output * weights).sum(axis=1) / weights.sum(axis=1)
        vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
        if (
            vectors.shape != (len(texts), LOCK["dimensions"])
            or not np.isfinite(vectors).all()
        ):
            raise ValueError("Invalid embedding output")
        return vectors.astype("<f4").tobytes()


def write_bundle(output: Path, members: dict[str, bytes]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    with zipfile.ZipFile(
        temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        for name, body in sorted(members.items()):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, body, compresslevel=9)
    temporary.replace(output)


def package(
    root: Path,
    sources: list[str],
    model: Path,
    cache: Path,
    output: Path,
    *,
    image_model: Path | None = None,
    image_selection: Path | None = None,
    observations: Path | None = None,
) -> dict:
    from filelock import FileLock

    cache.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        FileLock(cache / "writer.lock", timeout=0),
        FileLock(output.with_suffix(".lock"), timeout=0),
    ):
        return _package(
            root,
            sources,
            model,
            cache,
            output,
            image_model=image_model,
            image_selection=image_selection,
            observations=observations,
        )


def _package(
    root: Path,
    sources: list[str],
    model: Path,
    cache: Path,
    output: Path,
    *,
    image_model: Path | None = None,
    image_selection: Path | None = None,
    observations: Path | None = None,
) -> dict:
    if image_selection is not None and image_model is None:
        raise ValueError("Image selection requires an image model")
    if observations is not None and image_model is None:
        raise ValueError("Observations require an image model and gallery")
    entities, html, responses = collect_artifacts(root, sources)
    digest = content_digest(entities)
    format_version = (
        4
        if observations is not None
        else (3 if image_model is not None else FORMAT_VERSION)
    )
    recipe_spec = {"format": format_version, "search": SEARCH_VERSION, "model": LOCK}
    image_members: dict[str, bytes] = {}
    image_metadata: dict = {}
    observation_members: dict[str, bytes] = {}
    observation_metadata: dict = {}
    if image_model is not None:
        from pipelines.image_distribution import build_image_members

        image_members, image_metadata, _ = build_image_members(
            root, sources, entities, image_model, image_selection
        )
        recipe_spec["image"] = {
            "metadata": image_metadata,
            "files": {
                name: sha256(body) for name, body in sorted(image_members.items())
            },
        }
    if observations is not None:
        from pipelines.observation_distribution import build_observation_members

        observation_members, observation_metadata = build_observation_members(
            observations, image_members, image_metadata, model, cache
        )
        recipe_spec["observations"] = {
            "metadata": observation_metadata,
            "files": {
                name: sha256(body) for name, body in sorted(observation_members.items())
            },
        }
    recipe = sha256(canonical(recipe_spec))
    if output.exists():
        with zipfile.ZipFile(output) as previous:
            manifest = json.loads(previous.read("manifest.json"))
            if (
                manifest["content_sha256"] == digest
                and manifest["recipe_sha256"] == recipe
            ):
                if previous.testzip() is not None:
                    raise ValueError("Existing bundle failed integrity check")
                if image_model is not None:
                    from pipelines.image_distribution import validate_image_bundle

                    validate_image_bundle(previous, manifest, entities)
                if observations is not None:
                    from pipelines.observation_distribution import (
                        validate_observation_bundle,
                    )

                    validate_observation_bundle(previous, manifest)
                return {**manifest, "changed": False, "output": str(output)}
    encoder = Encoder(model)
    index = []
    chunks: list[dict] = []
    for entity in entities:
        texts = encoder.chunks(entity)
        index.append(
            {
                key: entity[key]
                for key in (
                    "id",
                    "title",
                    "url",
                    "source",
                    "kind",
                    "categories",
                    "aliases",
                )
            }
        )
        chunks.extend({"entity": len(index) - 1, **chunk} for chunk in texts)
    model_key = sha256(canonical(LOCK))
    keys = [sha256(model_key.encode() + chunk["text"].encode()) for chunk in chunks]
    missing = list(
        dict.fromkeys(key for key in keys if not (cache / f"{key}.f32").exists())
    )
    by_key = dict(zip(keys, chunks, strict=True))
    width = LOCK["dimensions"] * 4
    for start in range(0, len(missing), 16):
        batch = missing[start : start + 16]
        vectors = encoder.encode([by_key[key]["text"] for key in batch])
        for position, key in enumerate(batch):
            target = cache / f"{key}.f32"
            temporary = target.with_suffix(".tmp")
            temporary.write_bytes(vectors[position * width : (position + 1) * width])
            temporary.replace(target)
        print(
            f"Embedded {min(start + 16, len(missing))}/{len(missing)} new chunks",
            file=sys.stderr,
            flush=True,
        )
    vector_bytes = b"".join((cache / f"{key}.f32").read_bytes() for key in keys)
    if len(vector_bytes) != len(chunks) * width:
        raise ValueError("Embedding cache contains an invalid vector")
    members = {
        "index.json": canonical(index),
        "chunks.json": canonical(chunks),
        "vectors.f32": vector_bytes,
    }
    members.update(responses)
    for entity in entities:
        identifier = entity["id"].replace(":", "/")
        members[f"entities/{identifier}.json"] = canonical(entity)
    for identifier, body in html.items():
        members[f"html/{identifier}.html"] = body
    for name in LOCK["files"]:
        members[f"model/{name}"] = (model / name).read_bytes()
    # Include cross-runtime reference vectors for the release acceptance check.
    probes = [
        "russian cheeseboard",
        "airborne early warning radar",
        "coastal surveillance",
        "phased-array weather radar",
    ]
    members["probes.json"] = canonical(probes)
    members["probes.f32"] = encoder.encode(probes)
    members.update(image_members)
    members.update(observation_members)
    manifest = {
        "format_version": format_version,
        "content_sha256": digest,
        "recipe_sha256": recipe,
        "dataset_id": sha256((digest + recipe).encode()),
        "entities": len(entities),
        "evidence_pages": len(html),
        "chunks": len(chunks),
        "model": LOCK,
        "sources": sorted(set(sources)),
        "files": {name: sha256(body) for name, body in sorted(members.items())},
    }
    if image_model is not None:
        manifest["image"] = image_metadata
    if observations is not None:
        manifest["observations"] = observation_metadata
    members["manifest.json"] = canonical(manifest)
    if image_model is not None:
        from pipelines.image_distribution import validate_image_bundle

        pending = output.with_suffix(".pending.zip")
        try:
            write_bundle(pending, members)
            with zipfile.ZipFile(pending) as archive:
                validate_image_bundle(archive, manifest, entities)
                if observations is not None:
                    from pipelines.observation_distribution import (
                        validate_observation_bundle,
                    )

                    validate_observation_bundle(archive, manifest)
            pending.replace(output)
        finally:
            pending.unlink(missing_ok=True)
    else:
        write_bundle(output, members)
    return {**manifest, "changed": True, "output": str(output)}
