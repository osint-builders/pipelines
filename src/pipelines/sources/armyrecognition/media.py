import re
from collections.abc import Iterable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import BeautifulSoup, Tag

from pipelines.media import MediaCandidate, MediaReference
from pipelines.media_context import page_owners
from pipelines.sources.html import text

_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
_SECTIONS = {"desc", "data", "spec", "details", "photos"}


def _url(page: str, value: str) -> str:
    parts = urlsplit(urljoin(page, value))._replace(fragment="")
    return quote(urlunsplit(parts), safe="/:?=&%+@,;()-._~")


def _reason(url: str) -> str:
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "www.armyrecognition.com":
        return "unsupported_media_origin"
    if not any(parts.path.lower().endswith(suffix) for suffix in _FORMATS):
        return "unsupported_image_format"
    return ""


def _context(image: Tag) -> Tag | None:
    for parent in image.parents:
        if isinstance(parent, Tag) and (
            parent.get("id") in _SECTIONS
            or {"content-article-template", "margin-image-full"}.intersection(
                parent.get("class") or []
            )
        ):
            return parent
    return None


def candidates(url: str, body: bytes, entities: list[dict]) -> Iterable[MediaCandidate]:
    owners = page_owners(url, entities)
    if not owners:
        return
    soup = BeautifulSoup(body, "html.parser")
    main = soup.select_one("main")
    if main is None:
        raise ValueError("Army Recognition media page is missing its main content")
    for image in main.select("img[src]"):
        target = _url(url, str(image["src"]))
        link = image.find_parent("a", href=True)
        linked = _url(url, str(link["href"])) if link else ""
        context = _context(image)
        reason = ""
        if "/component/banners/" in linked or "/banners/" in target:
            reason = "advertisement"
        elif linked and "/military-products/" in linked and linked != url:
            reason = "cross_linked_entity"
        elif context is None:
            reason = "navigation_image"
        caption = str(image.get("alt", ""))
        paragraph = image.find_parent(["figure", "p"])
        explicit = paragraph.select_one("figcaption, strong") if paragraph else None
        if explicit:
            caption = text(explicit)
        heading = context.find(re.compile(r"^h[1-6]$")) if context else None
        section = (
            text(heading) if heading else str(context.get("id", "")) if context else ""
        )
        original = (
            linked
            if any(urlsplit(linked).path.lower().endswith(s) for s in _FORMATS)
            else target
        )
        references = (
            [
                MediaReference(identity, evidence, caption, section, len(owners) > 1)
                for identity, evidence in owners
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
        placeholder = "sigFreeImg" in (image.get("class") or [])
        yield MediaCandidate(
            target,
            references,
            reason or ("transparent_placeholder" if placeholder else _reason(target)),
            role="preview" if original != target else "original",
            original_url=original if original != target else "",
            page_url=url,
            caption=caption,
            section=section,
        )
        previews = set()
        if link and link.get("data-thumb"):
            previews.add(_url(url, str(link["data-thumb"])))
        picture = image.find_parent("picture")
        if picture:
            for node in picture.select("source[srcset]"):
                previews.update(
                    _url(url, match[0])
                    for match in re.findall(
                        r"(\S+)\s+(\d+(?:\.\d+)?)[wx](?:\s*,\s*|$)", str(node["srcset"])
                    )
                )
        for preview in sorted(previews - {target, original}):
            yield MediaCandidate(
                preview,
                references,
                reason
                or _reason(preview)
                or ("" if placeholder else "redundant_preview"),
                role="preview",
                original_url=original,
                page_url=url,
                caption=caption,
                section=section,
            )
