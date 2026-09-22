import hashlib
import json
import subprocess
import sys
import threading
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import override
from urllib.parse import urlsplit

import pytest
from scrapy.http import Response

from pipelines.archive import Archive, atomic_json
from pipelines.build import build
from pipelines.crawl import ArchiveSpider
from pipelines.model import Entity, EntityKind, Evidence
from pipelines.snapshot import load_snapshot
from pipelines.sources.radartutorial import Radartutorial


class FixtureSource(Radartutorial):
    id = "fixture"
    minimum_entities = 1

    def __init__(self, origin: str) -> None:
        self.origin = origin
        self.seeds = (f"{origin}/index.en.html",)

    @override
    def normalize(self, url: str) -> str | None:
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}" != self.origin or parts.path.startswith(
            "/outside/"
        ):
            return None
        if not parts.path.endswith(".en.html"):
            return None
        return self.origin + parts.path

    @override
    def extract(self, url: str, body: bytes, names: list[str]) -> list[Entity]:
        if not url.endswith("/radar.en.html"):
            return []
        return [
            Entity(
                key="weather-1",
                title="Fixture radar",
                kind=EntityKind.RADAR,
                evidence=[
                    Evidence(url=url, title="Fixture radar", markdown=body.decode())
                ],
            )
        ]


class BrokenDiscovery(FixtureSource):
    minimum_entities = 1

    @override
    def discover(self, url: str, body: bytes) -> list[str]:
        raise ValueError("Simulated adapter discovery failure")


class SupplementalFixture(FixtureSource):
    def discovery_seeds(self, directory: Path) -> list[str]:
        return [self.origin + "/radar.en.html"]


class PostFixture(FixtureSource):
    def request_body(self, url: str) -> bytes | None:
        return json.dumps({"page": urlsplit(url).path}).encode()

    def request_headers(self, url: str) -> dict[str, str]:
        return {"Content-Type": "application/json"}


@pytest.mark.parametrize("enabled", [False, True])
def test_pdf_capture_requires_source_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool
) -> None:
    source = FixtureSource("https://example.test")
    monkeypatch.setattr(
        source,
        "additional_content_types",
        ("application/pdf",) if enabled else (),
        raising=False,
    )
    monkeypatch.setattr(source, "discover", lambda url, body: [])
    with closing(Archive(tmp_path)) as archive:
        spider = ArchiveSpider(source, archive)
        request = spider.request(source.seeds[0])
        response = Response(
            source.seeds[0],
            request=request,
            status=200,
            headers={"Content-Type": "application/pdf; charset=binary"},
            body=b"%PDF-fixture",
        )
        assert list(spider.capture(response)) == []
        assert archive.counts() == {"saved" if enabled else "failed": 1}


def test_discovery_failure_during_resume_cannot_publish(tmp_path: Path) -> None:
    origin = "http://127.0.0.1:1"
    source_dir = tmp_path / "fixture"
    archive = Archive(source_dir / "archives" / "resume")
    url = origin + "/index.en.html"
    archive.add(url)
    archive.save(
        url,
        200,
        b'<div class="content"><h2>Index</h2><p>A complete archived page whose links must be discovered.</p></div>',
        "text/html",
        {},
    )
    archive.close()
    atomic_json(source_dir / "work.json", {"archive": "resume", "adapter_version": "1"})
    result = subprocess.run(
        [sys.executable, __file__, origin, str(tmp_path), "broken"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode != 0
    assert "Simulated adapter discovery failure" in result.stderr
    assert not (source_dir / "published" / "current.json").exists()
    assert not (source_dir / "archives" / "resume" / "complete.json").exists()


def test_real_crawl_obeys_robots_and_resumes_after_failure(tmp_path: Path) -> None:
    requests: list[str] = []
    broken = True

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requests.append(self.path)
            status = 200
            if self.path == "/robots.txt":
                body = b"User-agent: *\nDisallow: /denied.en.html\n"
            elif self.path == "/index.en.html":
                body = b"""<div class="content"><h2>Fixture index</h2><p>A useful collection of local radar documents.</p>
                <a href="denied.en.html">Denied</a>
                <a href="redirect.en.html">Redirect</a><a href="https://example.invalid/index.en.html">External</a></div>"""
            elif self.path == "/radar.en.html":
                status = 503 if broken else 200
                body = b'<div class="content"><h2>Fixture radar</h2><p>A sufficiently detailed description of a weather radar.</p></div>'
            elif self.path == "/redirect.en.html":
                self.send_response(302)
                self.send_header("Location", "/outside/target.en.html")
                self.end_headers()
                return
            else:
                status, body = 404, b"Unexpected request"
            self.send_response(status)
            self.send_header(
                "Content-Type",
                "text/plain" if self.path == "/robots.txt" else "text/html",
            )
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    command = [sys.executable, __file__, origin, str(tmp_path), "supplemental"]
    try:
        first = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert first.returncode != 0, first.stdout + first.stderr
        assert "Crawl incomplete" in first.stderr
        assert not (tmp_path / "fixture" / "published" / "current.json").exists()
        first_index_requests = requests.count("/index.en.html")
        broken = False
        second = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert second.returncode == 0, second.stdout + second.stderr
        assert requests.count("/index.en.html") == first_index_requests
        assert "/denied.en.html" not in requests
        assert "/outside/target.en.html" not in requests
        assert not (tmp_path / "fixture" / "work.json").exists()
        manifest, entities = load_snapshot(tmp_path / "fixture")
        assert entities[0]["title"] == "Fixture radar"
        archive_id = manifest["archive"]
        archive = Archive(tmp_path / "fixture" / "archives" / archive_id)
        assert archive.counts() == {"excluded": 2, "saved": 2}
        archive.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_post_catalog_pages_use_shared_archive_and_replay(tmp_path: Path) -> None:
    requests: list[tuple[str, bytes]] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.path == "/robots.txt"
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"User-agent: *\nDisallow:\n")

        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers["Content-Length"]))
            requests.append((self.path, body))
            assert self.headers["Content-Type"] == "application/json"
            assert json.loads(body) == {"page": self.path}
            body = (
                b'<div class="content"><h2>Equipment</h2><p>Complete catalog records.</p><a href="radar.en.html">Radar</a></div>'
                if self.path == "/index.en.html"
                else b'<div class="content"><h2>Fixture radar</h2><p>Complete equipment details and specifications.</p></div>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Set-Cookie", "private-session=not-archived")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        @override
        def log_message(self, format: str, *args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    origin = f"http://127.0.0.1:{server.server_port}"
    try:
        result = subprocess.run(
            [sys.executable, __file__, origin, str(tmp_path), "post"],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert len(requests) == 2
        manifest, before = load_snapshot(tmp_path / "fixture")
        source = PostFixture(origin)
        archive = Archive(tmp_path / "fixture/archives" / manifest["archive"])
        try:
            for page in archive.pages("saved"):
                headers = json.loads(page["headers"])
                assert headers["request-method"] == "POST"
                request_body = source.request_body(page["url"])
                assert request_body is not None
                assert (
                    headers["request-body-sha256"]
                    == hashlib.sha256(request_body).hexdigest()
                )
                assert "set-cookie" not in {key.lower() for key in headers}
        finally:
            archive.close()
        build(source, tmp_path, archive_id=manifest["archive"])
        assert load_snapshot(tmp_path / "fixture")[1] == before
        assert len(requests) == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


if __name__ == "__main__":
    source = (
        BrokenDiscovery(sys.argv[1])
        if len(sys.argv) > 3 and sys.argv[3] == "broken"
        else PostFixture(sys.argv[1])
        if len(sys.argv) > 3 and sys.argv[3] == "post"
        else SupplementalFixture(sys.argv[1])
    )
    build(source, Path(sys.argv[2]))
