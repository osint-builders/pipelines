import hashlib
import http.client
import json
import os
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from pipelines.archive import atomic_json
from pipelines.media import MAX_IMAGE_BYTES, MediaStore, _resolved_path
from pipelines.sources.base import AuthenticatedSource, Source

MAX_ATTEMPTS = 3
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = 30
CHUNK_BYTES = 64 * 1024
_TRANSIENT_STATUSES = {408, 429, 500, 502, 503, 504}


class _Failure(Exception):
    def __init__(
        self,
        code: str,
        *,
        transient: bool = False,
        status: int | None = None,
        delay: float = 0,
        retry_after: str | None = None,
    ) -> None:
        self.code = code
        self.transient = transient
        self.status = status
        self.delay = delay
        self.retry_after = retry_after


class _Restart(_Failure):
    def __init__(self) -> None:
        super().__init__("representation_changed", transient=True)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args: object, **kwargs: object) -> None:
        return None


def _origin(url: str) -> tuple[str, str, int]:
    try:
        parsed = urlsplit(url)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.fragment
            or any(character.isspace() for character in url)
        ):
            raise ValueError
        return (
            parsed.scheme,
            parsed.hostname.casefold(),
            parsed.port
            if parsed.port is not None
            else (443 if parsed.scheme == "https" else 80),
        )
    except ValueError:
        raise _Failure("invalid_media_url") from None


def _component(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", value):
        raise ValueError("Invalid media path component")
    return value


def _validator(headers: object) -> dict[str, str] | None:
    # Last-Modified is strong only when the response Date is at least 60s later.
    get = getattr(headers, "get")
    etag = str(get("ETag", ""))
    if re.fullmatch(r'"[^"\r\n]*"', etag):
        return {"header": "ETag", "value": etag}
    modified, date = str(get("Last-Modified", "")), str(get("Date", ""))
    try:
        modified_date, response_date = (
            parsedate_to_datetime(modified),
            parsedate_to_datetime(date),
        )
        if (
            modified_date.tzinfo
            and response_date.tzinfo
            and (response_date - modified_date).total_seconds() >= 60
        ):
            return {"header": "Last-Modified", "value": modified}
    except (ValueError, TypeError, OverflowError):
        pass
    return None


def _retry_after(value: str) -> tuple[float, str | None]:
    now = datetime.now(UTC)
    try:
        delay = (
            float(int(value))
            if value.isdigit()
            else (parsedate_to_datetime(value) - now).total_seconds()
        )
        delay = max(0.0, delay)
        when = datetime.fromtimestamp(now.timestamp() + delay, UTC).isoformat()
        return delay, when
    except (ValueError, TypeError, OverflowError, OSError):
        return 0, None


def _open(
    source: Source,
    url: str,
    allowed: set[tuple[str, str, int]],
    resume_headers: dict[str, str],
) -> tuple[object, str]:
    opener = urllib.request.build_opener(_NoRedirect())
    seed_origins = {_origin(seed) for seed in source.seeds}
    credential_origin = _origin(url) if _origin(url) in seed_origins else None
    visited: set[str] = set()
    for _ in range(MAX_REDIRECTS + 1):
        origin = _origin(url)
        if origin not in allowed:
            raise _Failure("media_origin_not_allowed")
        if url in visited:
            raise _Failure("redirect_loop")
        visited.add(url)
        headers = {"User-Agent": "pipelines-media/1", "Accept-Encoding": "identity"}
        if credential_origin == origin and isinstance(source, AuthenticatedSource):
            try:
                for key, value in source.request_headers(url).items():
                    if key.casefold() not in {
                        "host",
                        "connection",
                        "content-length",
                        "range",
                        "if-range",
                        "accept-encoding",
                    }:
                        headers[key] = value
            except Exception:
                raise _Failure("source_headers_failed") from None
        headers.update(resume_headers)
        try:
            response = opener.open(
                urllib.request.Request(url, headers=headers), timeout=REQUEST_TIMEOUT
            )
        except urllib.error.HTTPError as error:
            response = error
        except (OSError, ValueError, http.client.HTTPException):
            raise _Failure("network_error", transient=True) from None
        if response.status not in {301, 302, 303, 307, 308}:
            return response, url
        location = response.headers.get("Location", "")
        response.close()
        if not location:
            raise _Failure("redirect_without_location")
        next_url = urljoin(url, location)
        next_origin = _origin(next_url)
        if origin[0] == "https" and next_origin[0] != "https":
            raise _Failure("https_downgrade")
        if next_origin != credential_origin:
            credential_origin = None
        url = next_url
    raise _Failure("too_many_redirects")


def _remove_partial(partial: Path, metadata: Path) -> None:
    partial.unlink(missing_ok=True)
    metadata.unlink(missing_ok=True)


def _partial_state(partial: Path, metadata: Path, url: str) -> dict:
    try:
        state = json.loads(metadata.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise ValueError
        size = partial.stat().st_size
        validator = state.get("validator")
        if (
            state.get("version") == 1
            and state.get("url") == url
            and isinstance(state.get("expected_length"), int)
            and 0 < size < state["expected_length"] <= MAX_IMAGE_BYTES
            and isinstance(validator, dict)
            and validator.get("header") in {"ETag", "Last-Modified"}
            and isinstance(validator.get("value"), str)
            and "\r" not in validator["value"]
            and "\n" not in validator["value"]
            and state.get("sha256") == hashlib.sha256(partial.read_bytes()).hexdigest()
        ):
            _origin(state["final_url"])
            return state
    except (OSError, ValueError, TypeError, KeyError, _Failure):
        pass
    _remove_partial(partial, metadata)
    return {}


def _download(
    source: Source,
    allowed: set[tuple[str, str, int]],
    url: str,
    partial: Path,
    metadata: Path,
) -> dict:
    state = _partial_state(partial, metadata, url)
    offset = partial.stat().st_size if state else 0
    headers = (
        {"Range": f"bytes={offset}-", "If-Range": state["validator"]["value"]}
        if state
        else {}
    )
    response, final_url = _open(source, url, allowed, headers)
    try:
        status = response.status  # type: ignore[attr-defined]
        response_headers = response.headers  # type: ignore[attr-defined]
        if status not in {200, 206}:
            if status == 416 and offset:
                raise _Restart()
            delay, retry_after = _retry_after(response_headers.get("Retry-After", ""))
            raise _Failure(
                f"http_{status}",
                transient=status in _TRANSIENT_STATUSES,
                status=status,
                delay=delay,
                retry_after=retry_after,
            )
        content_type = (
            response_headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        )
        if not content_type.startswith("image/"):
            raise _Failure("not_image_content_type", status=status)
        if response_headers.get("Content-Encoding", "identity").lower() not in {
            "",
            "identity",
        }:
            raise _Failure("unsupported_content_encoding", status=status)
        try:
            length = (
                int(response_headers["Content-Length"])
                if "Content-Length" in response_headers
                else None
            )
            if length is not None and length < 0:
                raise ValueError
        except ValueError:
            raise _Failure("invalid_content_length", status=status) from None
        validator = _validator(response_headers)
        total: int | None
        if status == 206:
            match = re.fullmatch(
                r"bytes (\d+)-(\d+)/(\d+)", response_headers.get("Content-Range", "")
            )
            if not state or not match:
                raise _Restart()
            start, end, total = map(int, match.groups())
            if (
                start != offset
                or end != total - 1
                or total != state["expected_length"]
                or end < start
                or (length is not None and length != total - start)
                or validator != state["validator"]
                or final_url != state["final_url"]
                or content_type != state["content_type"]
            ):
                raise _Restart()
        else:
            offset, total = 0, length
        if total is not None and total > MAX_IMAGE_BYTES:
            raise _Failure("image_too_large", status=status)
        state = {
            "version": 1,
            "url": url,
            "final_url": final_url,
            "content_type": content_type,
            "expected_length": total,
            "validator": validator,
            "http_status": status,
        }
        atomic_json(metadata, state)
        count = offset
        try:
            with partial.open("ab" if offset else "wb") as handle:
                while True:
                    incomplete = False
                    try:
                        block = response.read(CHUNK_BYTES)  # type: ignore[attr-defined]
                    except http.client.IncompleteRead as error:
                        block, incomplete = error.partial, True
                    if count + len(block) > MAX_IMAGE_BYTES or (
                        total is not None and count + len(block) > total
                    ):
                        raise _Failure(
                            "image_too_large"
                            if count + len(block) > MAX_IMAGE_BYTES
                            else "response_length_mismatch",
                            status=status,
                        )
                    handle.write(block)
                    count += len(block)
                    if incomplete:
                        raise _Failure(
                            "interrupted_transfer", transient=True, status=status
                        )
                    if not block:
                        break
                handle.flush()
                os.fsync(handle.fileno())
            if total is not None and count != total:
                raise _Failure("interrupted_transfer", transient=True, status=status)
        except (OSError, http.client.HTTPException):
            raise _Failure(
                "interrupted_transfer", transient=True, status=status
            ) from None
        finally:
            if partial.exists():
                state["sha256"] = hashlib.sha256(partial.read_bytes()).hexdigest()
                atomic_json(metadata, state)
        return state
    finally:
        response.close()  # type: ignore[attr-defined]


def capture_media(source: Source, root: Path, archive_id: str) -> dict:
    """Capture registered media while the caller holds the source writer lock."""
    source_id, archive_id = _component(source.id), _component(archive_id)
    origins = getattr(source, "media_origins", ())
    allowed = {_origin(origin) for origin in origins}
    temporary = root / "media" / "tmp" / source_id / archive_id
    store = MediaStore(root)
    try:
        if not _resolved_path(temporary).is_relative_to(_resolved_path(root / "media")):
            raise ValueError("Media temporary directory escapes the archive")
        temporary.mkdir(parents=True, exist_ok=True)
        records = store.records(source_id, archive_id)
        for record in records:
            if record.get("retry_after"):
                try:
                    if datetime.fromisoformat(record["retry_after"]) > datetime.now(
                        UTC
                    ):
                        return store.manifest(source_id, archive_id)
                except (ValueError, TypeError):
                    pass
        for record in records:
            media_id = record["id"]
            filename = hashlib.sha256(media_id.encode()).hexdigest()
            if record["state"] in {"excluded", "unassociated"}:
                continue
            if record["state"] == "saved":
                try:
                    store.body(record["sha256"])
                    continue
                except (OSError, ValueError):
                    store.mark(
                        source_id,
                        archive_id,
                        media_id,
                        "failed",
                        "blob_integrity_mismatch",
                    )
            partial, metadata = (
                temporary / f"{filename}.part",
                temporary / f"{filename}.json",
            )
            if any(
                not _resolved_path(path).is_relative_to(_resolved_path(temporary))
                for path in (partial, metadata, metadata.with_suffix(".tmp"))
            ):
                raise ValueError("Media partial path escapes the temporary directory")
            stop = False
            for attempt in range(MAX_ATTEMPTS):
                try:
                    _origin(record["url"])
                    result = _download(
                        source, allowed, record["url"], partial, metadata
                    )
                    try:
                        store.save(
                            source_id,
                            archive_id,
                            media_id,
                            partial,
                            content_type=result["content_type"],
                            final_url=result["final_url"],
                            http_status=result["http_status"],
                        )
                    except (ValueError, OSError):
                        raise _Failure(
                            "invalid_image", status=result["http_status"]
                        ) from None
                    _remove_partial(partial, metadata)
                    break
                except _Failure as error:
                    if isinstance(error, _Restart) or not error.transient:
                        _remove_partial(partial, metadata)
                    deferred = error.delay > 60
                    store.mark(
                        source_id,
                        archive_id,
                        media_id,
                        "pending" if deferred else "failed",
                        error.code,
                        http_status=error.status,
                        retry_after=error.retry_after,
                    )
                    if (
                        deferred
                        or error.status in {401, 403}
                        or (error.status == 429 and attempt + 1 == MAX_ATTEMPTS)
                    ):
                        stop = True
                    if (
                        stop
                        or deferred
                        or not error.transient
                        or attempt + 1 == MAX_ATTEMPTS
                    ):
                        break
                    time.sleep(error.delay or min(attempt + 1, 2))
            if stop:
                break
        return store.manifest(source_id, archive_id)
    finally:
        store.close()
