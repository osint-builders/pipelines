import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.media_context import page_owners
from pipelines.sources.virtualglobetrotting import FEED

ORIGINS = ("https://c1.vgtstatic.com", "https://c2.vgtstatic.com")
_THUMB = re.compile(
    r"/(thumb(?:ll)?)/\d/\d/(\d+)-v(\d+)-(xl|l)/([^/]+\.(?:jpg|png|webp))$", re.I
)


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if url == FEED:
        return []
    soup = BeautifulSoup(body, "html.parser")
    coordinate = soup.select_one(".map-info-coordinates a[href]")
    identity = (
        re.fullmatch(
            r"/map/(\d+)/nearby/", urlsplit(urljoin(url, str(coordinate["href"]))).path
        )
        if coordinate
        else None
    )
    if identity is None:
        raise ValueError("Missing VirtualGlobetrotting map identity for media")
    owners = page_owners(
        url,
        (
            entity
            for entity in entities
            if entity["id"] == "virtualglobetrotting:" + identity[1]
        ),
    )
    rows = []
    for image in soup.select('img[src], meta[property="og:image"][content]'):
        target = urljoin(url, str(image.get("src", image.get("content", ""))))
        parts = urlsplit(target)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        match = _THUMB.fullmatch(parts.path)
        caption = str(image.get("alt", ""))
        selected = (
            image.name == "meta"
            or image.find_parent(class_="map-info-thumbs-l") is not None
        )
        reason = ""
        if parts.path == "/images/t.gif":
            reason = "placeholder_image"
        elif not selected:
            reason = (
                "unrelated_image"
                if "map-thumb" in str(image.get("class", ""))
                else "navigation_image"
            )
        elif not match or match[2] != identity[1]:
            reason = "unrelated_image"
        elif parts.scheme + "://" + parts.netloc not in ORIGINS:
            reason = "unsupported_media_origin"
        group = (
            (parts.netloc, match[1], match[2], match[3], match[5])
            if match and not reason
            else None
        )
        rows.append((target, caption, reason, group, match[4] if match else ""))
    groups = {}
    for target, _, reason, group, size in rows:
        if group is not None and not reason:
            if group not in groups or size == "xl":
                groups[group] = target
    captions = {
        group: caption
        for _, caption, reason, group, _ in rows
        if group and caption and not reason
    }
    result = []
    for target, caption, reason, group, _ in rows:
        original = groups.get(group, target) if group else target
        caption = caption or (captions.get(group, "") if group else "")
        references = (
            [
                MediaReference(owner, evidence, caption, "Map imagery")
                for owner, evidence in owners
            ]
            if not reason
            else []
        )
        result.append(
            MediaCandidate(
                target,
                references,
                reason,
                role="preview" if group else "original",
                original_url=original if group else "",
                page_url=url,
                caption=caption,
                section="Map imagery" if group else "Page navigation",
            )
        )
    return result
