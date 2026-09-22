import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from pipelines.media import (
    MAX_IMAGE_BYTES,
    MAX_IMAGE_PIXELS,
    MediaCandidate,
    MediaReference,
)
from pipelines.media_context import page_owners
from pipelines.sources.commons import clean, introduction
from pipelines.sources.html import text

_HOSTS = {"upload.wikimedia.org", "thumb.wikimedia.org"}
_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
_MIMES = {"image/jpeg", "image/png", "image/webp"}


def _url(page: str, value: str) -> str:
    resolved = urljoin(page, value)
    parts = urlsplit(resolved)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(character.isspace() for character in resolved)
    ):
        raise ValueError("Invalid Commons media URL")
    return resolved


def _reason(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in _HOSTS
        or parts.port not in {None, 443}
    ):
        return "unsupported_media_origin"
    if not any(parts.path.lower().endswith(suffix) for suffix in _FORMATS):
        return "unsupported_image_format"
    return ""


def _number(value: object) -> int:
    return int(str(value)) if str(value).isdigit() else 0


def _width(url: str, fallback: int = 0) -> int:
    match = re.search(r"/(\d+)px-", urlsplit(url).path)
    return int(match[1]) if match else fallback


def _preview_urls(page: str, container: Tag) -> dict[str, int]:
    result: dict[str, int] = {}
    for image in container.select("img[src]"):
        url = _url(page, str(image["src"]))
        rendered_width = _number(image.get("width", ""))
        result[url] = _width(url, rendered_width)
        srcset = str(image.get("srcset", ""))
        for target, value, unit in re.findall(
            r"(\S+)\s+(\d+(?:\.\d+)?)([wx])(?:\s*,\s*|$)", srcset
        ):
            url = _url(page, target)
            width = int(float(value) * (rendered_width if unit == "x" else 1))
            result[url] = _width(url, width)
    for link in container.select("a.mw-thumbnail-link[href]"):
        url = _url(page, str(link["href"]))
        dimensions = re.search(r"([\d,]+)\s*[×x]\s*[\d,]+", text(link))
        result[url] = _width(
            url, int(dimensions[1].replace(",", "")) if dimensions else 0
        )
    for video in container.select("video[poster]"):
        url = _url(page, str(video["poster"]))
        result[url] = _width(url, _number(video.get("width", "")))
    return result


def _caption(soup: BeautifulSoup) -> str:
    label = soup.select_one("#fileinfotpl_desc")
    cell = label.find_next_sibling("td") if label else None
    if cell:
        return text(clean(cell))
    parser = soup.select_one("#mw-content-text .mw-parser-output")
    if parser:
        return "\n\n".join(introduction(parser))
    return ""


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if not urlsplit(url).path.startswith("/wiki/File:"):
        return []
    soup = BeautifulSoup(body, "html.parser")
    container = soup.select_one("#file.fullImageLink")
    original_link = soup.select_one(".fullMedia a.internal[href]")
    if original_link is None and container:
        original_link = container.select_one("a[href]")
    if original_link is None:
        raise ValueError("Commons file page is missing its original media URL")
    original = _url(url, str(original_link["href"]))
    caption = _caption(soup)
    owners = page_owners(url, entities)
    ambiguous = len({entity for entity, _ in owners}) > 1
    references = [
        MediaReference(
            entity, evidence, caption, section="Description", ambiguous=ambiguous
        )
        for entity, evidence in owners
    ]
    reason = _reason(original)
    mime = soup.select_one(".fullMedia .mime-type")
    if not reason and mime and text(mime).lower() not in _MIMES:
        reason = "unsupported_image_format"
    info = soup.select_one(".fullMedia .fileInfo")
    size = (
        re.search(r"file size:\s*([\d.,]+)\s*(bytes?|[KMGT]i?B)", text(info), re.I)
        if info
        else None
    )
    if not reason and size:
        unit = size[2].upper()
        power = "KMGT".index(unit[0]) + 1 if unit[0] in "KMGT" else 0
        byte_count = float(size[1].replace(",", "")) * 1024**power
        if byte_count > MAX_IMAGE_BYTES:
            reason = "declared_image_too_large"
    image = container.select_one("img") if container else None
    if (
        not reason
        and image
        and (
            _number(image.get("data-file-width", ""))
            * _number(image.get("data-file-height", ""))
            > MAX_IMAGE_PIXELS
        )
    ):
        reason = "declared_image_too_many_pixels"
    result = [
        MediaCandidate(
            original,
            references,
            reason,
            role="original",
            page_url=url,
            caption=caption,
            section="Description",
        )
    ]
    previews = _preview_urls(url, container) if container else {}
    previews.pop(original, None)
    original_identity = urlsplit(original)[:3]
    eligible = {
        target: width
        for target, width in previews.items()
        if not _reason(target) and urlsplit(target)[:3] != original_identity
    }
    known = {target: width for target, width in eligible.items() if width > 0}
    bounded = {target: width for target, width in known.items() if width <= 1600}
    if bounded:
        selected = max(
            bounded, key=lambda target: (bounded[target], -len(target), target)
        )
    elif known:
        selected = min(known, key=lambda target: (known[target], len(target), target))
    else:
        selected = min(eligible, default="")
    for target in sorted(previews):
        result.append(
            MediaCandidate(
                target,
                references,
                _reason(target) or ("" if target == selected else "redundant_preview"),
                role="preview",
                original_url=original,
                page_url=url,
                caption=caption,
                section="Description",
            )
        )
    return result
