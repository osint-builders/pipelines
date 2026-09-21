import hashlib
import json
import threading
from collections.abc import Callable, Iterator
from email.utils import formatdate
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from PIL import Image

from pipelines import media_download
from pipelines.media import MediaCandidate, MediaReference, MediaStore
from pipelines.media_download import capture_media
from pipelines.model import Entity


class LocalSource:
    id = "test"
    version = "1"
    minimum_entities = 1

    def __init__(self, origin: str, *extra_origins: str) -> None:
        self.seeds: tuple[str, ...] = (origin + "/catalog",)
        self.media_origins = (origin, *extra_origins)
        self.auth_calls: list[str] = []

    def request_headers(self, url: str) -> dict[str, str]:
        self.auth_calls.append(url)
        return {
            "Authorization": "Basic sentinel-secret",
            "Cookie": "session=sentinel-secret",
            "X-CSRFToken": "sentinel-secret",
        }

    def normalize(self, url: str) -> str:
        return url

    def discover(self, url: str, body: bytes) -> list[str]:
        return []

    def labels(self, url: str, body: bytes) -> dict[str, list[str]]:
        return {}

    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        return []


class LocalServer:
    def __init__(self, handler: Callable[[BaseHTTPRequestHandler], None]) -> None:
        self.requests: list[tuple[str, dict[str, str]]] = []
        requests = self.requests

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                requests.append((self.path, dict(self.headers.items())))
                handler(self)

            def log_message(self, *args: object) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        self.thread.start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


@pytest.fixture
def servers() -> Iterator[
    Callable[[Callable[[BaseHTTPRequestHandler], None]], LocalServer]
]:
    active: list[LocalServer] = []

    def create(handler: Callable[[BaseHTTPRequestHandler], None]) -> LocalServer:
        server = LocalServer(handler)
        active.append(server)
        return server

    yield create
    for server in active:
        server.close()


@pytest.fixture
def png() -> bytes:
    output = BytesIO()
    image = Image.new("RGB", (16, 16))
    image.putdata([(x * 17, y * 17, (x + y) * 8) for y in range(16) for x in range(16)])
    image.save(output, format="PNG")
    return output.getvalue()


def reply(
    handler: BaseHTTPRequestHandler,
    body: bytes = b"",
    status: int = 200,
    *,
    headers: dict[str, str] | None = None,
    truncate: int | None = None,
) -> None:
    values = {
        "Content-Type": "image/png",
        "Content-Length": str(len(body)),
        "Date": formatdate(usegmt=True),
        **(headers or {}),
    }
    handler.send_response_only(status)
    for key, value in values.items():
        handler.send_header(key, value)
    handler.end_headers()
    handler.wfile.write(body[:truncate] if truncate is not None else body)
    handler.wfile.flush()
    handler.close_connection = True


def register(root: Path, *urls: str) -> None:
    with MediaStore(root) as store:
        store.register(
            "test",
            "run",
            [
                MediaCandidate(url, [MediaReference("test:entity", "evidence")])
                for url in urls
            ],
        )


def records(root: Path) -> list[dict]:
    with MediaStore(root) as store:
        return store.records("test", "run")


def test_completed_images_reuse_offline_and_never_store_headers(
    tmp_path: Path, servers: Any, png: bytes
) -> None:
    server = servers(lambda request: reply(request, png))
    source = LocalSource(server.origin)
    register(tmp_path, server.origin + "/image")
    capture_media(source, tmp_path, "run")
    first = records(tmp_path)[0]
    assert first["state"] == "saved"
    assert first["sha256"] == hashlib.sha256(png).hexdigest()
    source.media_origins = ()
    capture_media(source, tmp_path, "run")
    assert len(server.requests) == 1
    assert records(tmp_path)[0] == first
    assert not list((tmp_path / "media" / "tmp").rglob("*.part"))
    assert all(
        b"sentinel-secret" not in file.read_bytes()
        for file in (tmp_path / "media").rglob("*")
        if file.is_file()
    )


@pytest.mark.parametrize("validator", ["etag", "last_modified"])
def test_interrupted_transfer_resumes_across_runs(
    tmp_path: Path,
    servers: Any,
    png: bytes,
    monkeypatch: pytest.MonkeyPatch,
    validator: str,
) -> None:
    monkeypatch.setattr(media_download, "MAX_ATTEMPTS", 1)
    count = 0
    response_headers = (
        {"ETag": '"v1"'}
        if validator == "etag"
        else {"Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT"}
    )

    def serve(request: BaseHTTPRequestHandler) -> None:
        nonlocal count
        count += 1
        if count == 1:
            reply(request, png, headers=response_headers, truncate=40)
        else:
            assert request.headers["Range"] == "bytes=40-"
            assert request.headers["If-Range"] == next(iter(response_headers.values()))
            reply(
                request,
                png[40:],
                206,
                headers={
                    **response_headers,
                    "Content-Range": f"bytes 40-{len(png) - 1}/{len(png)}",
                },
            )

    server = servers(serve)
    register(tmp_path, server.origin + "/image")
    source = LocalSource(server.origin)
    capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["error"] == "interrupted_transfer"
    assert (
        list((tmp_path / "media" / "tmp").rglob("*.part"))[0].read_bytes() == png[:40]
    )
    capture_media(source, tmp_path, "run")
    record = records(tmp_path)[0]
    assert record["state"] == "saved"
    assert record["http_status"] == 206
    with MediaStore(tmp_path) as store:
        assert store.body(record["sha256"]) == png


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"ETag": 'W/"weak"'},
        {
            "Last-Modified": "Wed, 01 Jan 2025 00:00:00 GMT",
            "Date": "Wed, 01 Jan 2025 00:00:30 GMT",
        },
    ],
)
def test_unvalidated_partials_restart(
    tmp_path: Path,
    servers: Any,
    png: bytes,
    monkeypatch: pytest.MonkeyPatch,
    headers: dict[str, str],
) -> None:
    monkeypatch.setattr(media_download.time, "sleep", lambda _: None)
    count = 0

    def serve(request: BaseHTTPRequestHandler) -> None:
        nonlocal count
        count += 1
        assert request.headers.get("Range") is None
        reply(request, png, headers=headers, truncate=40 if count == 1 else None)

    server = servers(serve)
    register(tmp_path, server.origin + "/image")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    assert records(tmp_path)[0]["state"] == "saved"
    assert count == 2


@pytest.mark.parametrize(
    "change", ["status_200", "etag", "range", "total", "missing_range"]
)
def test_changed_or_malformed_resume_never_concatenates(
    tmp_path: Path,
    servers: Any,
    png: bytes,
    monkeypatch: pytest.MonkeyPatch,
    change: str,
) -> None:
    monkeypatch.setattr(media_download.time, "sleep", lambda _: None)
    count = 0

    def serve(request: BaseHTTPRequestHandler) -> None:
        nonlocal count
        count += 1
        if count == 1:
            reply(request, png, headers={"ETag": '"v1"'}, truncate=40)
        elif count == 2 and change != "status_200":
            values = {
                "ETag": '"v2"' if change == "etag" else '"v1"',
                "Content-Range": f"bytes {41 if change == 'range' else 40}-{len(png) - 1}/{len(png) + (change == 'total')}",
            }
            if change == "missing_range":
                values.pop("Content-Range")
            reply(request, png[40:], 206, headers=values)
        else:
            if count == 3:
                assert request.headers.get("Range") is None
            reply(request, png, headers={"ETag": '"v2"'})

    server = servers(serve)
    register(tmp_path, server.origin + "/image")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    record = records(tmp_path)[0]
    assert record["state"] == "saved"
    assert record["sha256"] == hashlib.sha256(png).hexdigest()
    assert count == (2 if change == "status_200" else 3)


def test_redirects_recompute_headers_and_drop_cross_origin_credentials(
    tmp_path: Path, servers: Any, png: bytes
) -> None:
    external = servers(lambda request: reply(request, png))

    def serve(request: BaseHTTPRequestHandler) -> None:
        if request.path == "/start":
            reply(
                request,
                status=302,
                headers={"Location": "/same", "Set-Cookie": "extra=unexpected"},
            )
        else:
            reply(request, status=302, headers={"Location": external.origin + "/image"})

    primary = servers(serve)
    source = LocalSource(primary.origin, external.origin)
    register(tmp_path, primary.origin + "/start")
    capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["state"] == "saved"
    assert source.auth_calls == [primary.origin + "/start", primary.origin + "/same"]
    assert primary.requests[1][1]["Cookie"] == "session=sentinel-secret"
    assert not {key.casefold() for key in external.requests[0][1]} & {
        "authorization",
        "cookie",
        "x-csrftoken",
    }


@pytest.mark.parametrize("mode", ["blocked", "loop"])
def test_redirects_validate_allowlist_and_loops(
    tmp_path: Path, servers: Any, png: bytes, mode: str
) -> None:
    external = servers(lambda request: reply(request, png))
    primary = servers(
        lambda request: reply(
            request,
            status=302,
            headers={
                "Location": external.origin + "/image"
                if mode == "blocked"
                else "/start"
            },
        )
    )
    register(tmp_path, primary.origin + "/start")
    capture_media(LocalSource(primary.origin), tmp_path, "run")
    assert records(tmp_path)[0]["error"] == (
        "media_origin_not_allowed" if mode == "blocked" else "redirect_loop"
    )
    assert len(primary.requests) == 1
    assert external.requests == []


@pytest.mark.parametrize("status", [401, 403])
def test_expired_auth_stops_source_and_preserves_pending_records(
    tmp_path: Path, servers: Any, status: int
) -> None:
    server = servers(lambda request: reply(request, status=status))
    register(tmp_path, server.origin + "/one", server.origin + "/two")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    assert len(server.requests) == 1
    assert sorted(record["state"] for record in records(tmp_path)) == [
        "failed",
        "pending",
    ]
    assert (
        next(record for record in records(tmp_path) if record["state"] == "failed")[
            "error"
        ]
        == f"http_{status}"
    )


def test_retry_after_is_honored_and_long_backoff_is_deferred(
    tmp_path: Path, servers: Any, png: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    delays: list[float] = []
    monkeypatch.setattr(media_download.time, "sleep", delays.append)
    count = 0

    def serve(request: BaseHTTPRequestHandler) -> None:
        nonlocal count
        count += 1
        reply(
            request,
            png if count > 1 else b"",
            200 if count > 1 else 503,
            headers={"Retry-After": "2"},
        )

    server = servers(serve)
    register(tmp_path, server.origin + "/image")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    assert records(tmp_path)[0]["state"] == "saved"
    assert delays == [2]
    other_root = tmp_path / "limited"
    limited = servers(
        lambda request: reply(request, status=429, headers={"Retry-After": "120"})
    )
    register(other_root, limited.origin + "/image", limited.origin + "/second")
    source = LocalSource(limited.origin)
    capture_media(source, other_root, "run")
    capture_media(source, other_root, "run")
    record = next(record for record in records(other_root) if record["error"])
    assert record["state"] == "pending"
    assert record["error"] == "http_429"
    assert record["retry_after"]
    assert len(limited.requests) == 1
    assert all(record["state"] == "pending" for record in records(other_root))
    assert delays == [2]


@pytest.mark.parametrize(
    "kind", ["html", "fake_image", "wrong_mime", "truncated", "oversized"]
)
def test_nonimages_and_invalid_images_remain_visible_failures(
    tmp_path: Path, servers: Any, png: bytes, kind: str
) -> None:
    body = (
        b"<html>Login</html>"
        if kind in {"html", "fake_image"}
        else png[:-12]
        if kind == "truncated"
        else png
    )
    headers = {
        "Content-Type": "text/html"
        if kind == "html"
        else "image/jpeg"
        if kind == "wrong_mime"
        else "image/png"
    }
    if kind == "oversized":
        headers["Content-Length"] = str(media_download.MAX_IMAGE_BYTES + 1)
    server = servers(lambda request: reply(request, body, headers=headers))
    register(tmp_path, server.origin + "/image")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    record = records(tmp_path)[0]
    assert record["state"] == "failed"
    assert record["error"] == (
        "not_image_content_type"
        if kind == "html"
        else "image_too_large"
        if kind == "oversized"
        else "invalid_image"
    )
    assert len(server.requests) == 1
    assert not list((tmp_path / "media" / "tmp").rglob("*.part"))


def test_source_header_errors_are_redacted(
    tmp_path: Path, servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    server = servers(lambda request: reply(request))
    source = LocalSource(server.origin)

    def broken(url: str) -> dict[str, str]:
        raise ValueError("Cookie: sentinel-secret")

    monkeypatch.setattr(source, "request_headers", broken)
    register(tmp_path, server.origin + "/image")
    report = capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["error"] == "source_headers_failed"
    assert "sentinel-secret" not in json.dumps(report)
    assert server.requests == []


def test_partial_directory_cannot_escape_archive(tmp_path: Path, servers: Any) -> None:
    server = servers(lambda request: reply(request))
    register(tmp_path, server.origin + "/image")
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (tmp_path / "media/tmp").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Creating directory symlinks is unavailable")
    with pytest.raises(ValueError, match="temporary directory escapes"):
        capture_media(LocalSource(server.origin), tmp_path, "run")
    assert list(outside.iterdir()) == []
    assert server.requests == []


def test_https_redirect_never_downgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requests: list[str] = []

    def open_request(request: Any, timeout: int) -> Any:
        requests.append(request.full_url)
        return SimpleNamespace(
            status=302,
            headers={"Location": "http://test.example/image"},
            close=lambda: None,
        )

    monkeypatch.setattr(
        media_download.urllib.request,
        "build_opener",
        lambda *_: SimpleNamespace(open=open_request),
    )
    source = LocalSource("https://test.example", "http://test.example")
    register(tmp_path, "https://test.example/image")
    capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["error"] == "https_downgrade"
    assert requests == ["https://test.example/image"]


def test_corrupt_completed_blob_is_recaptured(
    tmp_path: Path, servers: Any, png: bytes
) -> None:
    server = servers(lambda request: reply(request, png))
    source = LocalSource(server.origin)
    register(tmp_path, server.origin + "/image")
    capture_media(source, tmp_path, "run")
    record = records(tmp_path)[0]
    target = next(
        path for path in (tmp_path / "media" / "objects").rglob("*") if path.is_file()
    )
    target.write_bytes(b"corruption")
    capture_media(source, tmp_path, "run")
    assert len(server.requests) == 2
    assert target.read_bytes() == png
    assert records(tmp_path)[0]["sha256"] == record["sha256"]
    assert records(tmp_path)[0]["captured_at"] == record["captured_at"]


def test_transient_failures_have_bounded_retries(
    tmp_path: Path, servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_download.time, "sleep", lambda _: None)
    server = servers(lambda request: reply(request, status=503))
    register(tmp_path, server.origin + "/image")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    assert len(server.requests) == 3
    assert records(tmp_path)[0]["error"] == "http_503"


def test_exhausted_rate_limit_stops_other_source_requests(
    tmp_path: Path, servers: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_download.time, "sleep", lambda _: None)
    server = servers(lambda request: reply(request, status=429))
    register(tmp_path, server.origin + "/one", server.origin + "/two")
    capture_media(LocalSource(server.origin), tmp_path, "run")
    assert len(server.requests) == 3
    assert sorted(record["state"] for record in records(tmp_path)) == [
        "failed",
        "pending",
    ]


def test_credentials_stay_dropped_after_redirect_returns_to_seed_origin(
    tmp_path: Path, servers: Any, png: bytes
) -> None:
    primary: LocalServer
    external = servers(
        lambda request: reply(
            request, status=302, headers={"Location": primary.origin + "/image"}
        )
    )

    def serve(request: BaseHTTPRequestHandler) -> None:
        if request.path == "/start":
            reply(request, status=302, headers={"Location": external.origin + "/relay"})
        else:
            reply(request, png)

    primary = servers(serve)
    source = LocalSource(primary.origin, external.origin)
    register(tmp_path, primary.origin + "/start")
    capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["state"] == "saved"
    assert source.auth_calls == [primary.origin + "/start"]
    assert "Authorization" not in primary.requests[-1][1]


def test_modified_partial_restarts_without_range(
    tmp_path: Path, servers: Any, png: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(media_download, "MAX_ATTEMPTS", 1)
    count = 0

    def serve(request: BaseHTTPRequestHandler) -> None:
        nonlocal count
        count += 1
        assert request.headers.get("Range") is None
        reply(
            request,
            png,
            headers={"ETag": '"same"'},
            truncate=40 if count == 1 else None,
        )

    server = servers(serve)
    source = LocalSource(server.origin)
    register(tmp_path, server.origin + "/image")
    capture_media(source, tmp_path, "run")
    partial = next((tmp_path / "media" / "tmp").rglob("*.part"))
    partial.write_bytes(b"x" * 40)
    capture_media(source, tmp_path, "run")
    assert records(tmp_path)[0]["state"] == "saved"
    assert len(server.requests) == 2
