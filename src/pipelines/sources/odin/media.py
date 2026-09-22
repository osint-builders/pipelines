import json
import re
from collections.abc import Iterable, Iterator
from urllib.parse import quote, unquote, urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference

ORIGIN = "https://odin.t2com.army.mil"
_ASSET_ID = r"(?:[a-fA-F0-9]{32}|[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12})"
_PATH = re.compile(
    rf"/(?:dotcms/)?dA/{_ASSET_ID}/fileAsset/[^/]+"
    r"|/dotcms/images/[a-fA-F0-9]/[a-fA-F0-9]{2}/[^/]+"
)
_UNSUPPORTED = {"svg", "bmp", "tif", "tiff", "pdf", "avif", "ico"}


def media_url(value: str) -> str | None:
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or parts.netloc != "odin.t2com.army.mil"
        or parts.query
        or parts.fragment
        or any(character.isspace() or ord(character) < 32 for character in value)
        or not _PATH.fullmatch(parts.path)
    ):
        return None
    decoded = parts.path
    for _ in range(8):
        if (
            "\\" in decoded
            or any(part in {".", ".."} for part in decoded.split("/"))
            or any(ord(character) < 32 for character in decoded)
            or re.search(r"%2f|%5c", decoded, re.IGNORECASE)
        ):
            return None
        next_path = unquote(decoded)
        if next_path == decoded:
            return value
        decoded = next_path
    return None


def image_url(value: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing ODIN image URL")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Invalid ODIN image URL")
    value = value.strip()
    if any(part in {".", ".."} for part in urlsplit(value).path.split("/")):
        raise ValueError("Invalid ODIN image URL")
    if value.startswith(("/dA/", "dA/", "/images/", "images/")):
        value = "/dotcms/" + value.lstrip("/")
    target = urljoin(ORIGIN + "/", value)
    parts = urlsplit(target)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
    ):
        raise ValueError("Invalid ODIN image URL")
    return quote(target, safe="/:@%()-_.,~?=&")


def _caption(value: object) -> str:
    return " ".join(unquote(str(value or "")).split())


def _inline_images(
    value: object, context: tuple[str, ...]
) -> Iterator[tuple[str, str, str]]:
    if isinstance(value, dict):
        label = _caption(value.get("name"))
        if label:
            context += (label,)
        for nested in value.values():
            yield from _inline_images(nested, context)
    elif isinstance(value, list):
        for nested in value:
            yield from _inline_images(nested, context)
    elif isinstance(value, str) and re.search(r"<(?:img|picture)\b", value, re.I):
        soup = BeautifulSoup(value, "html.parser")
        for image in soup.select("img, picture source"):
            caption = _caption(image.get("alt") or image.get("title"))
            targets = []
            for attribute in ("src", "data-src"):
                if image.get(attribute):
                    targets.append(str(image[attribute]))
            for attribute in ("srcset", "data-srcset"):
                if image.get(attribute):
                    targets.extend(
                        item.strip().split()[0]
                        for item in str(image[attribute]).split(",")
                        if item.strip()
                    )
            if not targets:
                raise ValueError("Missing ODIN embedded image URL")
            for target in dict.fromkeys(targets):
                yield target, caption, " / ".join(context)


def record_images(record: dict) -> list[tuple[str, str, str]]:
    from pipelines.sources.odin import sections

    gallery = record.get("images")
    if gallery is None or gallery == "":
        gallery = []
    if isinstance(gallery, str):
        try:
            gallery = json.loads(gallery)
        except ValueError as exc:
            raise ValueError("Invalid ODIN image gallery JSON") from exc
    if not isinstance(gallery, list):
        raise ValueError("Invalid ODIN image gallery")
    result = []
    for image in gallery:
        if (
            not isinstance(image, dict)
            or not isinstance(image.get("url"), str)
            or not image["url"].strip()
        ):
            raise ValueError("Missing ODIN gallery image URL")
        result.append((image["url"], _caption(image.get("name")), "Images"))
    title = record.get("titleImage")
    if record.get("hasTitleImage") and title and title != "TITLE_IMAGE_NOT_FOUND":
        if not isinstance(title, str):
            raise ValueError("Invalid ODIN title image URL")
        result.append((title, "", "Title image"))
    result.extend(_inline_images(record.get("notes"), ("Notes",)))
    result.extend(_inline_images(sections(record), ()))
    return result


def candidates(url: str, body: bytes, entities: list[dict]) -> Iterable[MediaCandidate]:
    from pipelines.sources.odin import payload

    _, records = payload(body)
    owners: dict[str, list[str]] = {}
    for entity in entities:
        identity = entity["id"]
        if not identity.startswith("odin:"):
            continue
        record_id = identity.removeprefix("odin:")
        for evidence in entity["evidence"]:
            if evidence["url"] == url and evidence.get("record_id") == record_id:
                owners.setdefault(record_id, []).append(evidence["id"])
    for record in records:
        record_id = record["identifier"]
        for target, caption, section in record_images(record):
            target = image_url(target)
            reason = ""
            if media_url(target) is None:
                reason = "outside_media_scope"
            elif urlsplit(target).path.rsplit(".", 1)[-1].lower() in _UNSUPPORTED:
                reason = "unsupported_image_format"
            references = [
                MediaReference("odin:" + record_id, evidence_id, caption, section)
                for evidence_id in sorted(set(owners.get(record_id, [])))
            ]
            yield MediaCandidate(
                target,
                references,
                reason,
                page_url=url,
                caption=caption,
                section=section,
            )
