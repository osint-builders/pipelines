from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.sources.cambridgepixel import HEADERS, SEED, identity
from pipelines.sources.html import text


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if url != SEED:
        return []
    soup = BeautifulSoup(body, "html.parser")
    owners = {
        entity["id"]: evidence["id"]
        for entity in entities
        for evidence in entity["evidence"]
        if evidence["url"] == url
    }
    result = []
    for image in soup.select('img[src], meta[property="og:image"][content]'):
        target = urljoin(url, str(image.get("src", image.get("content", ""))))
        parts = urlsplit(target)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        caption = str(image.get("alt", ""))
        references = []
        reason = "navigation_image"
        if "/icons/radar-types/" in parts.path:
            reason = "category_icon"
        elif "/logo/" in parts.path:
            reason = "site_logo"
        else:
            row = image.find_parent("tr")
            table = row.find_parent("table") if row else None
            if table and [text(h) for h in table.select("thead th")] == HEADERS:
                cells = row.find_all("td", recursive=False) if row else []
                if len(cells) == len(HEADERS):
                    for cell in cells[:2]:
                        for label in cell.select(".show-mobile"):
                            label.decompose()
                    owner = "cambridgepixel:" + identity(text(cells[0]), text(cells[1]))
                    if owner in owners:
                        references = [
                            MediaReference(owner, owners[owner], caption, "Radar model")
                        ]
                    reason = ""
        if not reason:
            if parts.scheme != "https" or parts.netloc != "cambridgepixel.com":
                reason = "unsupported_media_origin"
            elif not parts.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                reason = "unsupported_image_format"
        result.append(
            MediaCandidate(
                target,
                references,
                reason,
                page_url=url,
                caption=caption,
                section="Radar catalog",
            )
        )
    return result
