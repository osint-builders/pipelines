import re
from collections.abc import Iterable
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.sources.html import text
from pipelines.sources.russianforces import pattern

_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}


def _url(page: str, value: str) -> str:
    parts = urlsplit(urljoin(page, value))._replace(fragment="")
    return quote(urlunsplit(parts), safe="/:?=&%+@,;()-._~")


def _reason(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "russianforces.org":
        return "unsupported_media_origin"
    if not any(parts.path.lower().endswith(suffix) for suffix in _FORMATS):
        return "unsupported_image_format"
    return ""


def candidates(url: str, body: bytes, entities: list[dict]) -> Iterable[MediaCandidate]:
    owners = [
        (entity, page)
        for entity in entities
        for page in entity["evidence"]
        if page["url"] == url
    ]
    if not owners:
        return
    soup = BeautifulSoup(body, "html.parser")
    content = soup.select_one('.entry-asset[id^="entry-"] .asset-content')
    if content is None:
        raise ValueError("RussianForces media page is missing its article")
    for image in soup.select("img[src]"):
        target = _url(url, str(image["src"]))
        link = image.find_parent("a", href=True)
        linked = _url(url, str(link["href"])) if link else ""
        original = (
            linked
            if any(urlsplit(linked).path.lower().endswith(s) for s in _FORMATS)
            else target
        )
        caption = str(image.get("alt", ""))
        figure = image.find_parent("figure")
        explicit = figure.select_one("figcaption") if figure else None
        if explicit:
            caption = text(explicit)
        filename = bool(
            re.fullmatch(r"[^\r\n]+\.(?:jpe?g|png|webp|gif|svg)", caption, re.I)
        )
        identity_text = unquote(caption).replace("_", " ") if filename else caption
        # A dated filename names the report subject, not necessarily the pictured object.
        matched = [
            (entity, page)
            for entity, page in owners
            if pattern([entity["title"], *entity.get("aliases", [])]).search(
                identity_text
            )
        ]
        reason = ""
        if not any(parent is content for parent in image.parents):
            reason = "navigation_image"
        heading = image.find_previous(re.compile(r"^h[1-6]$"))
        section = text(heading) if heading else ""
        references = (
            [
                MediaReference(
                    entity["id"],
                    page["id"],
                    caption,
                    section,
                    ambiguous=filename or len(matched) > 1,
                    association="source_filename" if filename else "source_caption",
                )
                for entity, page in matched
            ]
            if not reason
            else []
        )
        if original != target:
            yield MediaCandidate(
                original,
                references,
                reason or _reason(original),
                page_url=url,
                caption=caption,
                section=section,
            )
        yield MediaCandidate(
            target,
            references,
            reason or _reason(target),
            role="preview" if original != target else "original",
            original_url=original if original != target else "",
            page_url=url,
            caption=caption,
            section=section,
        )
