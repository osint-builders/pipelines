import hashlib
from collections.abc import AsyncIterator, Iterator
from typing import Self, cast, override
from urllib.parse import urlsplit

from scrapy import Request, Spider
from scrapy.crawler import Crawler, CrawlerProcess
from scrapy.exceptions import CloseSpider, IgnoreRequest
from scrapy.http import Response
from twisted.python.failure import Failure

from pipelines.archive import Archive
from pipelines.sources.base import (
    AuthenticatedSource,
    PostSource,
    Source,
    SupplementalDiscovery,
)


class ScopeMiddleware:
    def __init__(self, crawler: Crawler) -> None:
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Crawler) -> Self:
        return cls(crawler)

    def process_request(self, request: Request) -> None:
        spider = cast(ArchiveSpider, self.crawler.spider)
        if request.url.endswith("/robots.txt"):
            origins = {urlsplit(seed)[:2] for seed in spider.source.seeds}
            if urlsplit(request.url)[:2] in origins:
                return
        if spider.source.normalize(request.url) != request.url:
            raise IgnoreRequest(f"Outside source scope: {request.url}")
        if isinstance(spider.source, AuthenticatedSource):
            request.headers.update(spider.source.request_headers(request.url))


class ArchiveSpider(Spider):
    name = "reference_archive"

    def __init__(
        self, source: Source, archive: Archive, discovery_urls: list[str] | None = None
    ) -> None:
        super().__init__()
        self.source = source
        self.archive = archive
        self.scheduled: set[str] = set()
        self.saved = 0
        self.discovery_urls = discovery_urls or []

    def request(self, url: str) -> Request | None:
        if url in self.scheduled:
            return None
        self.scheduled.add(url)
        self.archive.add(url)
        body = (
            self.source.request_body(url)
            if isinstance(self.source, PostSource) and self.source.normalize(url) == url
            else None
        )
        if body is not None and not isinstance(body, bytes):
            raise ValueError("Source POST body must be bytes")
        return Request(
            url,
            method="POST" if body is not None else "GET",
            body=body,
            callback=self.capture,
            errback=self.failed,
            meta={"archive_url": url},
            priority=100 if url in self.source.seeds else 0,
            dont_filter=True,
        )

    @override
    async def start(self) -> AsyncIterator[Request]:
        pending = {str(page["url"]) for page in self.archive.pages("pending", "failed")}
        saved = self.archive.pages("saved")
        self.scheduled.update(
            str(page["url"])
            for page in self.archive.pages("saved", "unavailable", "excluded")
        )
        pending.update(self.source.seeds)
        pending.update(self.discovery_urls)
        # Replaying saved discovery repairs a crash between saving and scheduling links.
        for page in saved:
            pending.update(self.source.discover(page["url"], self.archive.body(page)))
        for url in sorted(
            pending,
            key=lambda candidate: (candidate not in self.source.seeds, candidate),
        ):
            request = self.request(url)
            if request is not None:
                yield request

    def capture(self, response: Response) -> Iterator[Request]:
        original = str(response.meta["archive_url"])
        content_type = (response.headers.get(b"Content-Type") or b"").decode("latin1")
        headers = {
            key.decode("latin1"): b", ".join(values).decode("latin1")
            for key, values in response.headers.items()
            if key.lower() not in {b"set-cookie", b"set-cookie2"}
        }
        headers["effective-url"] = response.url
        if response.request is not None and response.request.method != "GET":
            headers["request-method"] = response.request.method
            headers["request-body-sha256"] = hashlib.sha256(
                response.request.body
            ).hexdigest()
        self.archive.save(
            original, response.status, response.body, content_type, headers
        )
        if response.status in {401, 403} and isinstance(
            self.source, AuthenticatedSource
        ):
            self.archive.fail(original, "Source rejected authentication")
            self.archive.checkpoint()
            raise CloseSpider("source-access-denied")
        if response.status != 200:
            return
        self.saved += 1
        if self.saved == 1 or self.saved % 100 == 0:
            self.archive.checkpoint()
        if self.saved % 100 == 0:
            self.logger.warning(
                "Archived %s pages; %s URLs discovered", self.saved, len(self.scheduled)
            )
        if not any(kind in content_type.lower() for kind in ("html", "xml", "json")):
            self.archive.fail(original, f"Unexpected content type: {content_type}")
            return
        try:
            discovered = self.source.discover(response.url, response.body)
        except PermissionError:
            self.archive.fail(original, "Source access expired or content restricted")
            self.archive.checkpoint()
            raise CloseSpider("source-access-denied") from None
        except Exception as exc:
            self.archive.fail(original, str(exc))
            self.archive.checkpoint()
            raise
        for url in discovered:
            request = self.request(url)
            if request is not None:
                yield request

    def failed(self, failure: Failure) -> None:
        request = cast(Request, getattr(failure, "request"))
        self.archive.fail(
            str(request.meta["archive_url"]),
            failure.getErrorMessage(),
            excluded=isinstance(failure.value, IgnoreRequest),
        )
        self.archive.checkpoint()


def crawl(source: Source, archive: Archive) -> None:
    if isinstance(source, AuthenticatedSource):
        # Validate request headers before starting the crawl.
        for seed in source.seeds:
            source.request_headers(seed)
    discovery_urls = (
        source.discovery_seeds(archive.path)
        if isinstance(source, SupplementalDiscovery)
        else []
    )
    process = CrawlerProcess(
        {
            "USER_AGENT": "OSINTBuildersPipelines/0.1 (+https://github.com/osint-builders/pipelines)",
            "ROBOTSTXT_OBEY": True,
            "CONCURRENT_REQUESTS": 2,
            "CONCURRENT_REQUESTS_PER_DOMAIN": 2,
            "DOWNLOAD_DELAY": 0.5,
            "AUTOTHROTTLE_ENABLED": True,
            "AUTOTHROTTLE_START_DELAY": 0.5,
            "AUTOTHROTTLE_MAX_DELAY": 30,
            "AUTOTHROTTLE_TARGET_CONCURRENCY": 1,
            "DOWNLOAD_TIMEOUT": 45,
            "DOWNLOAD_MAXSIZE": 10 * 1024 * 1024,
            "RETRY_TIMES": 3,
            "HTTPERROR_ALLOW_ALL": True,
            "LOG_LEVEL": "WARNING",
            "TELNETCONSOLE_ENABLED": False,
            "COOKIES_ENABLED": False,
            "DOWNLOADER_MIDDLEWARES": {"pipelines.crawl.ScopeMiddleware": 50},
        }
    )
    crawler = process.create_crawler(ArchiveSpider)
    process.crawl(
        crawler, source=source, archive=archive, discovery_urls=discovery_urls
    )
    process.start()
    if crawler.stats.get_value("finish_reason") != "finished":
        raise RuntimeError(
            "Crawl interrupted; repeat the explicit scrape command to resume"
        )
    if crawler.stats.get_value("spider_exceptions/count", 0):
        raise RuntimeError(
            "Crawler raised an extraction/discovery error; archive retained"
        )
    if archive.pages("pending", "failed"):
        raise RuntimeError(
            f"Crawl incomplete: {archive.counts()}; archive retained for resume"
        )
    if crawler.stats.get_value("log_count/ERROR", 0):
        raise RuntimeError("Crawler reported errors; archive retained for resume")
    saved_urls = {page["url"] for page in archive.pages("saved")}
    if not set(source.seeds).issubset(saved_urls):
        raise RuntimeError(
            "One or more discovery seeds were unavailable; refusing publication"
        )
    archive.mark_complete(source.id)
