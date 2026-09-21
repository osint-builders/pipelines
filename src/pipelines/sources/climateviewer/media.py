from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.sources.climateviewer import DATA, MAP, description, features, marker_key

_HOSTS = {
    "img37.imageshack.us",
    "img41.imageshack.us",
    "img651.imageshack.us",
    "img685.imageshack.us",
    "img718.imageshack.us",
    "i45.tinypic.com",
    "i46.tinypic.com",
    "i47.tinypic.com",
    "i50.tinypic.com",
}
ORIGINS = tuple(
    f"{scheme}://{host}" for host in sorted(_HOSTS) for scheme in ("http", "https")
)
_SECTION = (
    "Historical site description / Equipment illustration; site depiction unverified"
)


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    result = []
    if url == MAP:
        soup = BeautifulSoup(body, "html.parser")
        for image in soup.select('img[src], meta[property="og:image"][content]'):
            target = urljoin(url, str(image.get("src", image.get("content", ""))))
            if urlsplit(target).scheme not in {"http", "https"}:
                continue
            result.append(
                MediaCandidate(
                    target,
                    exclusion_reason="collection_image"
                    if image.find_parent(class_="post-content")
                    else "navigation_image",
                    page_url=url,
                    caption=str(image.get("alt", "")),
                    section="Map collection",
                )
            )
        return result
    if url != DATA:
        return []
    owners = {
        (entity["id"], evidence.get("record_id")): evidence["id"]
        for entity in entities
        for evidence in entity["evidence"]
        if evidence["url"] == url
    }
    for feature in features(body):
        if feature["geometry"]["type"] != "Point":
            continue
        key = marker_key(feature)
        owner = "climateviewer:" + key
        raw = feature["properties"]["description"]
        _, plain, _ = description(raw)
        for image in BeautifulSoup(raw, "html.parser").select("img[src]"):
            target = urljoin(url, str(image["src"]))
            parts = urlsplit(target)
            if parts.scheme not in {"http", "https"} or not parts.hostname:
                continue
            caption = str(image.get("alt", "")) or plain.splitlines()[0]
            reason = ""
            references = []
            if "/flags/" in parts.path or parts.path.endswith("/flagrussia.gif"):
                reason = "country_flag"
            elif parts.path.endswith("/nopicm.gif"):
                reason = "placeholder_image"
            else:
                if evidence := owners.get((owner, key)):
                    references = [
                        MediaReference(
                            owner, evidence, caption, _SECTION, ambiguous=True
                        )
                    ]
                if parts.hostname not in _HOSTS or parts.port not in {None, 80, 443}:
                    reason = "unsupported_media_origin"
                elif not parts.path.lower().endswith(
                    (".jpg", ".jpeg", ".png", ".webp")
                ):
                    reason = "unsupported_image_format"
            result.append(
                MediaCandidate(
                    target,
                    references,
                    reason,
                    page_url=url,
                    caption=caption,
                    section=_SECTION,
                )
            )
    return result
