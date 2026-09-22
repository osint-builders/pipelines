import re
from copy import copy
from dataclasses import asdict
from urllib.parse import unquote, urljoin, urlsplit

from bs4 import Tag

from pipelines.media import MAX_IMAGE_PIXELS, MediaCandidate, MediaReference
from pipelines.media_context import page_owners
from pipelines.sources.html import text

_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}
_LAYOUT = {
    "navbox",
    "navbar",
    "vertical-navbox",
    "sidebar",
    "hatnote",
    "ambox",
    "metadata",
    "sistersitebox",
    "sisterproject",
    "noprint",
    "stub",
    "stubbox",
    "reflist",
    "references",
    "mw-editsection",
    "flagicon",
    "info-icon",
    "portal",
    "portalbox",
    "mw-indicators",
}
_END_SECTIONS = {
    "references",
    "notes",
    "citations",
    "bibliography",
    "sources",
    "external links",
    "see also",
    "further reading",
}


def _url(page: str, value: str) -> str:
    target = urljoin(page, value)
    parts = urlsplit(target)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or any(character.isspace() for character in target)
    ):
        return ""
    try:
        parts.port
    except ValueError:
        return ""
    return target


def _format(url: str) -> str:
    path = urlsplit(url).path.split("/revision/", 1)[0]
    return "." + path.rsplit(".", 1)[-1].lower() if "." in path else ""


def _reason(url: str, hosts: set[str]) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.hostname not in hosts
        or parts.port not in {None, 443}
    ):
        return "unsupported_media_origin"
    return "" if _format(url) in _FORMATS else "unsupported_image_format"


def _is_original(url: str, hosts: set[str]) -> bool:
    parts = urlsplit(url)
    return (
        parts.hostname in hosts
        and bool(_format(url))
        and "/thumb/" not in parts.path
        and not re.search(r"/revision/[^/]+/", parts.path)
    )


def _number(value: object) -> int:
    return int(str(value)) if str(value).isdigit() else 0


def _heading(image: Tag, content: Tag, levels: list[str]) -> str:
    heading = image.find_previous(levels)
    if heading is None or content not in heading.parents:
        return ""
    cleaned = copy(heading)
    for node in cleaned.select(".mw-editsection"):
        node.decompose()
    return text(cleaned)


def _section(image: Tag, content: Tag) -> str:
    if image.find_parent("table", class_="infobox"):
        return "Infobox"
    return _heading(image, content, ["h2", "h3", "h4"]) or "Lead"


def _caption(image: Tag) -> str:
    container = (
        image.find_parent("figure")
        or image.find_parent(class_="gallerybox")
        or image.find_parent(class_="thumb")
        or image.find_parent("td")
    )
    if container:
        caption = container.select_one(
            "figcaption, .thumbcaption, .gallerytext, .infobox-caption"
        )
        if caption:
            return text(caption)
    return str(image.get("data-caption") or image.get("alt") or "").strip()


def _context_reason(image: Tag, content: Tag, section: str) -> str:
    for node in [image, *image.parents]:
        if not isinstance(node, Tag):
            continue
        if (
            node.name in {"script", "style", "form"}
            or set(node.get_attribute_list("class")) & _LAYOUT
        ):
            return "layout_image"
    names = " ".join(
        str(image.get(key, ""))
        for key in ("alt", "resource", "data-image-name", "src", "data-src")
    )
    names = unquote(names).replace("_", " ").lower()
    if re.search(r"\b(flag of|flagicon|logo|stub icon|red pog|location map)\b", names):
        return "layout_image"
    if (
        section.lower() in _END_SECTIONS
        or _heading(image, content, ["h2"]).lower() in _END_SECTIONS
    ):
        return "unrelated_section"
    width, height = _number(image.get("width")), _number(image.get("height"))
    if width and height and max(width, height) <= 64:
        return "layout_image"
    return ""


def discover_images(
    url: str, content: Tag, entities: list[dict], hosts: set[str]
) -> list[MediaCandidate]:
    owners = page_owners(url, entities)
    if not owners:
        return []
    result: list[MediaCandidate] = []
    for image in content.select("img"):
        sources: dict[str, int] = {}
        width = _number(image.get("width"))
        for attr in ("src", "data-src"):
            if target := _url(url, str(image.get(attr, ""))):
                if image.get(attr):
                    sources[target] = width
        for attr in ("srcset", "data-srcset"):
            for target, value, unit in re.findall(
                r"(\S+)\s+(\d+(?:\.\d+)?)([wx])(?:\s*,\s*|$)",
                str(image.get(attr, "")),
            ):
                if resolved := _url(url, target):
                    sources[resolved] = int(
                        float(value) * (width if unit == "x" else 1)
                    )
        if not sources:
            continue
        link = image.find_parent("a", href=True)
        linked = _url(url, str(link["href"])) if link else ""
        original = linked if linked and _is_original(linked, hosts) else ""
        if not original:
            original = next(
                (target for target in sources if _is_original(target, hosts)), ""
            )
        caption, section = _caption(image), _section(image, content)
        context_reason = _context_reason(image, content, section)
        references = [
            MediaReference(
                entity,
                evidence,
                caption,
                section,
                ambiguous=len({owner for owner, _ in owners}) > 1,
            )
            for entity, evidence in owners
        ]
        if original:
            reason = context_reason or _reason(original, hosts)
            pixels = _number(image.get("data-file-width")) * _number(
                image.get("data-file-height")
            )
            if not reason and pixels > MAX_IMAGE_PIXELS:
                reason = "declared_image_too_many_pixels"
            result.append(
                MediaCandidate(
                    original,
                    references,
                    reason,
                    page_url=url,
                    caption=caption,
                    section=section,
                )
            )
        previews = {
            target: size for target, size in sources.items() if target != original
        }
        eligible = {
            target: size
            for target, size in previews.items()
            if not _reason(target, hosts)
        }
        bounded = {
            target: size for target, size in eligible.items() if 0 < size <= 1600
        }
        selected = (
            max(bounded, key=lambda target: (bounded[target], target))
            if bounded
            else min(
                eligible, key=lambda target: (eligible[target], target), default=""
            )
        )
        for target in sorted(previews):
            reason = context_reason or _reason(target, hosts)
            result.append(
                MediaCandidate(
                    target,
                    references,
                    reason or ("" if target == selected else "redundant_preview"),
                    role="preview",
                    original_url=original or target,
                    page_url=url,
                    caption=caption,
                    section=section,
                )
            )
    # Lazy and noscript markup can repeat the same recorded occurrence.
    unique = {repr(asdict(candidate)): candidate for candidate in result}
    return [unique[key] for key in sorted(unique)]
