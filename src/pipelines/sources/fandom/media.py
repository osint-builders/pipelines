from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate
from pipelines.sources.fandom import payload
from pipelines.sources.wiki_media import discover_images

HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org", "static.wikia.nocookie.net"}


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if not entities:
        return []
    fragment = payload(body).get("parse", {}).get("text", {}).get("*")
    if not isinstance(fragment, str):
        raise ValueError("Fandom media requires its archived rendered article")
    soup = BeautifulSoup(fragment, "html.parser")
    content = soup.select_one(".mw-parser-output")
    if content is None:
        raise ValueError("Fandom media requires its archived article body")
    return discover_images(url, content, entities, HOSTS)
