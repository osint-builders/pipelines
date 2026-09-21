import json
import re
from collections.abc import Iterable, Iterator
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference

ORIGIN = "https://militaryperiscope.com"
_PREFIX = "/wt/media/original_images/"
_FORMATS = {"jpg", "jpeg", "png", "webp"}


def media_url(value: str) -> str | None:
    parts = urlsplit(value)
    decoded = unquote(parts.path)
    if (
        parts.scheme != "https"
        or parts.netloc != "militaryperiscope.com"
        or not parts.path.startswith(_PREFIX)
        or parts.query
        or parts.fragment
        or "\\" in decoded
        or any(part in {".", ".."} for part in decoded.split("/"))
        or any(ord(character) < 32 for character in decoded)
        or re.search(r"%2f|%5c|%25", parts.path, re.IGNORECASE)
    ):
        return None
    return value


def _absolute(value: str) -> str:
    url = urljoin(ORIGIN, value)
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.query
        or parts.fragment
        or any(ord(character) < 32 for character in url)
    ):
        raise ValueError("Invalid Military Periscope image URL")
    return quote(url, safe="/:@%()-_.,~")


def _caption(image: dict) -> str:
    parts = []
    for key in ("title", "caption"):
        text = image.get(key)
        if text and str(text) not in parts:
            parts.append(str(text))
    if image.get("source"):
        parts.append("Source: " + str(image["source"]))
    return "\n".join(parts)


def _heading(context: tuple[str, ...], title: str) -> tuple[str, ...]:
    return (
        context + (title,)
        if title and (not context or context[-1] != title)
        else context
    )


def _images(
    value: object, context: tuple[str, ...], labels: dict
) -> Iterator[tuple[dict, tuple[str, ...]]]:
    if isinstance(value, dict):
        kind = value.get("type")
        if kind == "image":
            image = value.get("value")
            if not isinstance(image, dict) or not image.get("path"):
                raise ValueError("Missing Military Periscope image data")
            yield image, context
            return
        if isinstance(value.get("path"), str) and (
            "width" in value
            or "height" in value
            or value["path"].startswith("/wt/media/")
        ):
            yield value, context
            return
        if isinstance(kind, str) and "value" in value:
            content = value["value"]
            if kind == "images" and not isinstance(content, list):
                raise ValueError("Invalid Military Periscope image gallery")
            if kind in {"section", "subsection", "styled_block"} and isinstance(
                content, dict
            ):
                title = content.get("header") or content.get("style", "")
                context = _heading(context, str(title))
                context = _heading(context, str(content.get("subheader", "")))
            elif kind not in {"content", "paragraph", "table"}:
                context = _heading(
                    context, labels.get(kind, kind.replace("_", " ").title())
                )
            yield from _images(content, context, labels)
            return
        context = _heading(context, str(value.get("title") or ""))
        for key, nested in value.items():
            if key in {"image", "photo"} and nested is not None:
                if not isinstance(nested, dict) or not nested.get("path"):
                    raise ValueError("Missing Military Periscope image data")
                yield nested, context
            else:
                yield from _images(nested, context, labels)
    elif isinstance(value, list):
        for nested in value:
            yield from _images(nested, context, labels)
    elif isinstance(value, str) and "<img" in value.lower():
        soup = BeautifulSoup(value, "html.parser")
        for image in soup.select("img[src]"):
            yield (
                {"path": str(image["src"]), "title": str(image.get("alt", ""))},
                context,
            )


def candidates(url: str, body: bytes, entities: list[dict]) -> Iterable[MediaCandidate]:
    if urlsplit(url).path == "/trial-access/":
        soup = BeautifulSoup(body, "html.parser")
        for logo in soup.select("img[src]"):
            yield MediaCandidate(
                _absolute(str(logo["src"])),
                exclusion_reason="navigation_image",
                page_url=url,
                caption=str(logo.get("alt", "")),
                section="Trial access navigation",
            )
        return
    data = json.loads(body)
    props = data.get("component_props")
    if props is None:
        return
    if not isinstance(props, dict):
        raise ValueError("Invalid Military Periscope media page")
    owners = sorted(
        {
            (entity["id"], page["id"])
            for entity in entities
            for page in entity["evidence"]
            if page["url"] == url
        }
    )
    ambiguous = len({identity for identity, _ in owners}) > 1
    labels = props.get("readable_name") or {}
    for image, context in _images(props, (), labels):
        if not isinstance(image.get("path"), str) or not image["path"]:
            raise ValueError("Missing Military Periscope image path")
        target = _absolute(image["path"])
        caption = _caption(image)
        section = " / ".join(context)
        reason = ""
        if media_url(target) is None:
            reason = "outside_media_scope"
        elif urlsplit(target).path.rsplit(".", 1)[-1].lower() not in _FORMATS:
            reason = "unsupported_image_format"
        references = [
            MediaReference(
                identity, evidence, caption, section=section, ambiguous=ambiguous
            )
            for identity, evidence in owners
        ]
        yield MediaCandidate(
            target, references, reason, page_url=url, caption=caption, section=section
        )
