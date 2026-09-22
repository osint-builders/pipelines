import hashlib
import json
import os
import re
import sqlite3
import tempfile
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Self, cast
from urllib.parse import urlsplit

from filelock import FileLock
from PIL import Image, ImageOps

from pipelines.model import valid_key

MEDIA_SCHEMA_VERSION = 1
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_IMAGE_PIXELS = 40_000_000
MAX_IMAGE_FRAMES = 64
NEAR_DUPLICATE_DISTANCE = 5
GENERIC_IMAGE_CONTENT_TYPES = frozenset({"", "unknown", "application/octet-stream"})
_STATES = {"pending", "saved", "failed", "excluded", "unassociated"}
_MIMES = {
    "JPEG": "image/jpeg",
    "PNG": "image/png",
    "WEBP": "image/webp",
    "GIF": "image/gif",
    "MPO": "image/mpo",
}


class MediaValidationError(ValueError):
    def __init__(self, message: str, code: str = "invalid_image") -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MediaReference:
    entity_id: str
    evidence_id: str
    caption: str = ""
    section: str = ""
    ambiguous: bool = False
    association: str = "source_context"


@dataclass(frozen=True)
class MediaCandidate:
    url: str
    references: list[MediaReference] = field(default_factory=list)
    exclusion_reason: str = ""
    role: str = "original"
    original_url: str = ""
    page_url: str = ""
    caption: str = ""
    section: str = ""
    embedded_body: bytes | None = field(default=None, repr=False)
    embedded_content_type: str = ""


@dataclass(frozen=True)
class ProcessingRecipe:
    version: str
    model_revision: str = ""
    settings: dict = field(default_factory=dict)
    model_id: str = ""


def _json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value: str) -> None:
    if re.fullmatch(r"[a-f0-9]{64}", value) is None:
        raise ValueError("Expected a lowercase SHA-256 digest")


def _resolved_path(path: Path) -> Path:
    resolved = str(path.resolve())
    if os.name == "nt":
        # Windows may retain the extended prefix while a parent is being created.
        if resolved.startswith("\\\\?\\UNC\\"):
            resolved = "\\\\" + resolved[8:]
        elif resolved.startswith("\\\\?\\") and re.match(r"[A-Za-z]:\\", resolved[4:]):
            resolved = resolved[4:]
    return Path(resolved)


def _scope(source: str, archive: str) -> None:
    if not valid_key(source) or not valid_key(archive):
        raise ValueError("Media source and archive require safe keys")


def _url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(character.isspace() for character in url)
    ):
        raise ValueError(
            "Media requires an HTTP(S) URL without credentials or fragment"
        )
    try:
        parts.port
    except ValueError as exc:
        raise ValueError("Invalid media URL port") from exc


def media_id(source: str, url: str) -> str:
    _scope(source, "identity")
    _url(url)
    return f"{source}:media:{hashlib.sha256(url.encode()).hexdigest()[:24]}"


def recipe_key(sha256: str, recipe: ProcessingRecipe) -> str:
    _digest(sha256)
    if not recipe.version.strip():
        raise ValueError("A processing recipe requires a version")
    return hashlib.sha256(
        _json({"sha256": sha256, "recipe": asdict(recipe)}).encode()
    ).hexdigest()


def _decode(body: bytes, content_type: str) -> dict:
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise MediaValidationError(
            "Image exceeds the byte limit or is empty", "image_too_large"
        )
    mime = content_type.split(";", 1)[0].strip().lower()
    try:
        with Image.open(BytesIO(body)) as image:
            expected_mime = _MIMES.get(image.format or "")
            if expected_mime is None:
                raise MediaValidationError(
                    "Unsupported image format; expected JPEG, PNG, WEBP, static GIF, or MPO",
                    "unsupported_image_format",
                )
            if mime not in _MIMES.values() and mime not in GENERIC_IMAGE_CONTENT_TYPES:
                raise MediaValidationError(
                    "Image MIME declaration is unsupported",
                    "image_mime_mismatch",
                )
            if image.width * image.height > MAX_IMAGE_PIXELS:
                raise MediaValidationError(
                    "Image exceeds the decoded pixel limit", "decoded_image_too_large"
                )
            frame_count = getattr(image, "n_frames", 1)
            if image.format != "MPO" and frame_count != 1:
                raise MediaValidationError(
                    "Animated images are not supported", "animated_image"
                )
            if type(frame_count) is not int or not 1 <= frame_count <= MAX_IMAGE_FRAMES:
                raise MediaValidationError(
                    "Image exceeds the frame limit", "image_frame_limit"
                )
            if image.format == "MPO":
                total_pixels = 0
                unreadable_frames = []
                for frame in range(frame_count):
                    try:
                        image.seek(frame)
                    except ValueError as exc:
                        # Some primary JPEGs retain stale MPF thumbnail offsets.
                        if frame and str(exc) == "No data found for frame":
                            unreadable_frames.append(frame)
                            continue
                        raise
                    total_pixels += image.width * image.height
                    if total_pixels > MAX_IMAGE_PIXELS:
                        raise MediaValidationError(
                            "Image frames exceed the aggregate decoded pixel limit",
                            "decoded_image_too_large",
                        )
                    image.load()
            else:
                image.verify()
        with Image.open(BytesIO(body)) as image:
            image.load()
            normalized = ImageOps.exif_transpose(image)
            width, height = normalized.size
            sample = normalized.convert("L").resize((9, 8), Image.Resampling.LANCZOS)
            pixels = cast(list[int], list(sample.get_flattened_data()))
            bits = 0
            for y in range(8):
                for x in range(8):
                    bits = (bits << 1) | (pixels[y * 9 + x] > pixels[y * 9 + x + 1])
    except MediaValidationError:
        raise
    except (
        OSError,
        SyntaxError,
        ValueError,
        EOFError,
        Image.DecompressionBombError,
    ) as exc:
        raise MediaValidationError("Invalid or truncated image") from exc
    return {
        "content_type": expected_mime,
        **(
            {
                "frame_count": frame_count,
                "validated_frame_count": frame_count - len(unreadable_frames),
                "unreadable_frames": unreadable_frames,
            }
            if expected_mime == "image/mpo"
            else {}
        ),
        "width": width,
        "height": height,
        "sha256": hashlib.sha256(body).hexdigest(),
        "bytes": len(body),
        "perceptual_hash": f"{bits:016x}",
        "perceptual_hash_recipe": "exif-transpose-grayscale-lanczos-dhash-64-v1",
    }


def _write(path: Path, body: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class MediaStore:
    def __init__(self, root: Path, *, read_only: bool = False) -> None:
        self.root = _resolved_path(root)
        self.directory = self.root / "media"
        if not _resolved_path(self.directory).is_relative_to(self.root):
            raise ValueError("Media directory escapes the data root")
        self.read_only = read_only
        path = self.directory / "manifest.sqlite"
        if not _resolved_path(path).is_relative_to(_resolved_path(self.directory)):
            raise ValueError("Media manifest escapes the archive")
        if read_only:
            self.db = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=30)
        else:
            self.directory.mkdir(parents=True, exist_ok=True)
            self.db = sqlite3.connect(path, timeout=30)
        self.db.row_factory = sqlite3.Row
        try:
            if not read_only:
                self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            tables = self.db.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            if version != MEDIA_SCHEMA_VERSION:
                if version != 0 or tables or read_only:
                    raise ValueError(f"Unsupported media schema version: {version}")
                self.db.execute("""
                        CREATE TABLE media (
                            source TEXT NOT NULL, archive TEXT NOT NULL,
                            id TEXT NOT NULL, data TEXT NOT NULL,
                            PRIMARY KEY (source, archive, id)
                        )
                """)
                self.db.execute("""
                        CREATE TABLE derived (
                            key TEXT PRIMARY KEY, data TEXT NOT NULL
                        )
                """)
                self.db.execute(f"PRAGMA user_version={MEDIA_SCHEMA_VERSION}")
            required = {"media", "derived"}
            names = {
                row[0]
                for row in self.db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            if not required.issubset(names):
                raise ValueError("Incomplete media manifest schema")
            if read_only:
                self.db.execute("PRAGMA query_only=ON")
            else:
                self.db.commit()
                self.db.execute("PRAGMA journal_mode=WAL")
        except Exception:
            self.db.close()
            raise

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self.db.close()

    def _writable(self) -> None:
        if self.read_only:
            raise ValueError("Media store is read-only")

    def _path(self, category: str, digest: str) -> Path:
        _digest(digest)
        path = self.directory / category / digest[:2] / digest
        if not _resolved_path(path).is_relative_to(_resolved_path(self.directory)):
            raise ValueError("Media path escapes the archive")
        return path

    def _lock(self, category: str, digest: str) -> FileLock:
        path = self._path("locks", digest)
        path.parent.mkdir(parents=True, exist_ok=True)
        return FileLock(str(path) + f".{category}.lock", timeout=30)

    def _record(self, source: str, archive: str, identity: str) -> dict:
        _scope(source, archive)
        row = self.db.execute(
            "SELECT data FROM media WHERE source=? AND archive=? AND id=?",
            (source, archive, identity),
        ).fetchone()
        if row is None:
            raise ValueError("Unknown media record")
        value = json.loads(row["data"])
        if value.get("schema_version") != MEDIA_SCHEMA_VERSION:
            raise ValueError("Unsupported media record version")
        return value

    def _put(self, value: dict) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO media(source,archive,id,data) VALUES (?,?,?,?)",
            (value["source"], value["archive"], value["id"], _json(value)),
        )

    def register(
        self, source: str, archive: str, candidates: Iterable[MediaCandidate]
    ) -> None:
        self._writable()
        _scope(source, archive)
        grouped: dict[str, list[MediaCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[media_id(source, candidate.url)].append(candidate)

        def occurrence_key(item: dict) -> str:
            return _json(
                {
                    key: val
                    for key, val in item.items()
                    if key
                    not in {
                        "references",
                        "reference_contexts",
                        "associated",
                        "exclusion_reason",
                    }
                }
            )

        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            for identity, items in grouped.items():
                row = self.db.execute(
                    "SELECT data FROM media WHERE source=? AND archive=? AND id=?",
                    (source, archive, identity),
                ).fetchone()
                value: dict
                if row is None:
                    value = {
                        "schema_version": MEDIA_SCHEMA_VERSION,
                        "id": identity,
                        "source": source,
                        "archive": archive,
                        "url": items[0].url,
                        "references": [],
                        "state": "pending",
                        "captured_at": None,
                        "content_type": None,
                        "final_url": "",
                        "http_status": None,
                        "retry_after": None,
                        "width": None,
                        "height": None,
                        "sha256": None,
                        "bytes": None,
                        "perceptual_hash": None,
                        "error": "",
                    }
                else:
                    value = self._record(source, archive, identity)
                previous = [
                    {
                        "section": "",
                        "ambiguous": False,
                        "association": "source_context",
                        **reference,
                    }
                    for reference in value["references"]
                ]
                combined = {_json(reference): reference for reference in previous}
                occurrences = {
                    occurrence_key(item): item for item in value.get("occurrences", [])
                }
                eligible = sum(
                    item["associated"] and not item["exclusion_reason"]
                    for item in occurrences.values()
                )
                reasons = Counter(
                    item["exclusion_reason"]
                    for item in occurrences.values()
                    if item["exclusion_reason"]
                )
                for candidate in items:
                    if candidate.role not in {"original", "preview"}:
                        raise ValueError("Media role must be original or preview")
                    if candidate.role == "preview" and not candidate.original_url:
                        raise ValueError("Media previews require an original URL")
                    for url in (candidate.original_url, candidate.page_url):
                        if url:
                            _url(url)
                    for reference in candidate.references:
                        prefix, separator, native = reference.entity_id.partition(":")
                        if (
                            prefix != source
                            or not separator
                            or not valid_key(native)
                            or not valid_key(reference.evidence_id)
                        ):
                            raise ValueError(
                                "Media references require source-qualified IDs"
                            )
                        row_reference = asdict(reference)
                        combined[_json(row_reference)] = row_reference
                    occurrence = {
                        "page_url": candidate.page_url,
                        "role": candidate.role,
                        "original_url": candidate.original_url or candidate.url,
                        "exclusion_reason": candidate.exclusion_reason,
                        "associated": bool(candidate.references),
                        "caption": candidate.caption,
                        "section": candidate.section,
                        "references": [
                            {"entity_id": ref.entity_id, "evidence_id": ref.evidence_id}
                            for ref in candidate.references
                        ],
                        "reference_contexts": [
                            asdict(ref) for ref in candidate.references
                        ],
                    }
                    key = occurrence_key(occurrence)
                    old = occurrences.get(key)
                    if old:
                        eligible -= bool(
                            old["associated"] and not old["exclusion_reason"]
                        )
                        if reason := old["exclusion_reason"]:
                            reasons[reason] -= 1
                            if not reasons[reason]:
                                del reasons[reason]
                    pairs = {
                        _json(ref): ref
                        for ref in (old or {}).get("references", [])
                        + occurrence["references"]
                    }
                    occurrence["references"] = [pairs[key] for key in sorted(pairs)]
                    contexts = {
                        _json(ref): ref
                        for ref in (old or {}).get("reference_contexts", [])
                        + occurrence["reference_contexts"]
                    }
                    occurrence["reference_contexts"] = [
                        contexts[key] for key in sorted(contexts)
                    ]
                    occurrence["associated"] = bool(occurrence["references"])
                    occurrences[key] = occurrence
                    eligible += bool(
                        occurrence["associated"] and not occurrence["exclusion_reason"]
                    )
                    if reason := occurrence["exclusion_reason"]:
                        reasons[reason] += 1
                    if value["state"] != "saved":
                        if eligible:
                            if value["state"] in {"excluded", "unassociated"}:
                                value.update(state="pending", error="")
                        elif reasons:
                            value.update(
                                state="excluded", error="; ".join(sorted(reasons))
                            )
                        elif not combined:
                            value.update(state="unassociated", error="")
                value["references"] = [combined[key] for key in sorted(combined)]
                value["occurrences"] = [occurrences[key] for key in sorted(occurrences)]
                self._put(value)

    def records(self, source: str, archive: str) -> list[dict]:
        _scope(source, archive)
        result = []
        for row in self.db.execute(
            "SELECT id FROM media WHERE source=? AND archive=? ORDER BY id",
            (source, archive),
        ):
            result.append(self._record(source, archive, row["id"]))
        return result

    def body(self, sha256: str) -> bytes:
        path = self._path("objects", sha256)
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Archived media exceeds the byte limit")
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != sha256:
            raise ValueError("Archived media checksum mismatch")
        return body

    def save(
        self,
        source: str,
        archive: str,
        identity: str,
        path: Path,
        *,
        content_type: str,
        response_content_type: str | None = None,
        final_url: str = "",
        http_status: int = 200,
    ) -> dict:
        self._writable()
        if http_status not in {200, 206}:
            raise ValueError("Only successful complete image responses can be saved")
        if final_url:
            _url(final_url)
        if path.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError("Image exceeds the byte limit")
        body = path.read_bytes()
        metadata = _decode(body, content_type)
        digest = metadata["sha256"]
        with self._lock("object", digest), self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self._record(source, archive, identity)
            if (
                value["state"] in {"excluded", "unassociated"}
                or not value["references"]
            ):
                raise ValueError("Excluded or unassociated media cannot be saved")
            if value["sha256"] and value["sha256"] != digest:
                raise ValueError(
                    "A saved media record cannot change within its archive"
                )
            target = self._path("objects", digest)
            try:
                self.body(digest)
            except (OSError, ValueError):
                _write(target, body)
            value.update(metadata)
            if response_content_type is not None:
                value["response_content_type"] = response_content_type
            value.update(
                state="saved",
                captured_at=value["captured_at"] or datetime.now(UTC).isoformat(),
                final_url=final_url or value["url"],
                http_status=http_status,
                retry_after=None,
                error="",
            )
            self._put(value)
        return value

    def mark(
        self,
        source: str,
        archive: str,
        identity: str,
        state: str,
        error: str,
        *,
        http_status: int | None = None,
        retry_after: str | None = None,
        final_url: str = "",
    ) -> None:
        self._writable()
        if state not in _STATES - {"saved"}:
            raise ValueError("Invalid media state; successful downloads must use save")
        if final_url:
            _url(final_url)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            value = self._record(source, archive, identity)
            if state in {"pending", "failed"} and not value["references"]:
                raise ValueError("Unassociated media cannot be queued for download")
            if value["state"] == "saved":
                if state != "failed":
                    raise ValueError(
                        "Saved media can only be invalidated after corruption"
                    )
                try:
                    self.body(value["sha256"])
                except (OSError, ValueError):
                    pass
                else:
                    raise ValueError("Verified saved media cannot be marked failed")
            value.update(
                state=state,
                error=error,
                http_status=http_status,
                retry_after=retry_after,
            )
            if final_url:
                value["final_url"] = final_url
            self._put(value)

    def manifest(self, source: str, archive: str) -> dict:
        records = self.records(source, archive)
        counts = dict.fromkeys(sorted(_STATES), 0)
        hashes: dict[str, list[str]] = {}
        for record in records:
            counts[record["state"]] += 1
            if record["state"] == "saved":
                digest = record["sha256"]
                hashes.setdefault(digest, []).append(record["id"])
        return {
            "schema_version": MEDIA_SCHEMA_VERSION,
            "source": source,
            "archive": archive,
            "counts": {"discovered": len(records), **counts},
            "records": records,
            "exact_duplicates": [ids for ids in hashes.values() if len(ids) > 1],
            "near_duplicates": self._near_duplicates(set(hashes)),
            "near_duplicate_distance": NEAR_DUPLICATE_DISTANCE,
        }

    def _near_duplicates(self, scoped: set[str]) -> list[dict]:
        if not scoped:
            return []
        perceptual: dict[str, int] = {}
        references: dict[str, list[dict]] = {}
        for row in self.db.execute("SELECT data FROM media ORDER BY source,archive,id"):
            record = json.loads(row["data"])
            if record.get("schema_version") != MEDIA_SCHEMA_VERSION:
                raise ValueError("Unsupported media record version")
            if record["state"] == "saved":
                digest = record["sha256"]
                perceptual[digest] = int(record["perceptual_hash"], 16)
                references.setdefault(digest, []).append(
                    {key: record[key] for key in ("source", "archive", "id")}
                )
        buckets: dict[tuple[int, int], set[str]] = {}
        for digest, value in perceptual.items():
            for band in range(6):
                buckets.setdefault((band, (value >> (11 * band)) & 2047), set()).add(
                    digest
                )
        near = []
        seen: set[tuple[str, str]] = set()
        for left in sorted(scoped):
            candidates: set[str] = set()
            for band in range(6):
                candidates.update(
                    buckets[(band, (perceptual[left] >> (11 * band)) & 2047)]
                )
            for right in sorted(candidates - {left}):
                pair = (min(left, right), max(left, right))
                if pair in seen:
                    continue
                seen.add(pair)
                distance = (perceptual[left] ^ perceptual[right]).bit_count()
                if distance <= NEAR_DUPLICATE_DISTANCE:
                    near.append(
                        {
                            "left": sorted({ref["id"] for ref in references[left]}),
                            "right": sorted({ref["id"] for ref in references[right]}),
                            "left_records": references[left],
                            "right_records": references[right],
                            "distance": distance,
                        }
                    )
        return near

    def derive(
        self,
        sha256: str,
        recipe: ProcessingRecipe,
        producer: Callable[[Path, Path], None],
    ) -> Path:
        self._writable()
        original = self.body(sha256)
        recipe_data = json.loads(_json(asdict(recipe)))
        recipe = ProcessingRecipe(**recipe_data)
        key = recipe_key(sha256, recipe)
        target = self._path("derived", key)
        with self._lock("derived", key):
            row = self.db.execute(
                "SELECT data FROM derived WHERE key=?", (key,)
            ).fetchone()
            if row is not None:
                metadata = json.loads(row["data"])
                if metadata.get("schema_version") != MEDIA_SCHEMA_VERSION:
                    raise ValueError("Unsupported derived artifact version")
                if (
                    metadata.get("input_sha256") != sha256
                    or metadata.get("recipe") != recipe_data
                ):
                    raise ValueError("Derived artifact recipe mismatch")
                if (
                    hashlib.sha256(target.read_bytes()).hexdigest()
                    != metadata["sha256"]
                ):
                    raise ValueError("Derived artifact checksum mismatch")
                return target
            temporary_root = self.directory / "tmp"
            if not _resolved_path(temporary_root).is_relative_to(
                _resolved_path(self.directory)
            ):
                raise ValueError("Media temporary directory escapes the archive")
            temporary_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
                input_path = Path(directory) / "original"
                output_path = Path(directory) / "output"
                input_path.write_bytes(original)
                producer(input_path, output_path)
                body = output_path.read_bytes()
                if not body:
                    raise ValueError("Derived artifact is empty")
                metadata = {
                    "schema_version": MEDIA_SCHEMA_VERSION,
                    "input_sha256": sha256,
                    "recipe": recipe_data,
                    "sha256": hashlib.sha256(body).hexdigest(),
                    "bytes": len(body),
                }
                _write(target, body)
                with self.db:
                    self.db.execute(
                        "INSERT INTO derived(key,data) VALUES (?,?)",
                        (key, _json(metadata)),
                    )
        return target
