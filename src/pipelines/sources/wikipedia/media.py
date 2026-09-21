from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate
from pipelines.sources.wiki_media import discover_images

HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if not entities:
        return []
    soup = BeautifulSoup(body, "html.parser")
    content = soup.select_one("#mw-content-text .mw-parser-output, .mw-parser-output")
    if content is None:
        raise ValueError("Wikipedia media requires its archived article body")
    return discover_images(url, content, entities, HOSTS)
