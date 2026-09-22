import re
from collections.abc import Iterable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from bs4 import Tag

from pipelines.media import MediaCandidate, MediaReference
from pipelines.sources.html import text
from pipelines.sources.radartutorial import parse_html

ORIGIN = "https://www.radartutorial.eu"
_FORMATS = {".jpg", ".jpeg", ".png", ".webp"}


def _url(page: str, value: str) -> str:
    parts = urlsplit(urljoin(page, value))._replace(fragment="")
    if (
        parts.hostname in {"www.radartutorial.eu", "radartutorial.eu"}
        and parts.username is None
        and parts.port in {None, 80, 443}
    ):
        parts = parts._replace(scheme="https", netloc="www.radartutorial.eu")
    return quote(urlunsplit(parts), safe="/:?=&%+@,;()-._~#")


def _reason(url: str) -> str:
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != "www.radartutorial.eu"
        or parts.query
        or parts.fragment
    ):
        return "unsupported_media_origin"
    if not any(parts.path.lower().endswith(suffix) for suffix in _FORMATS):
        return "unsupported_image_format"
    return ""


def _container(image: Tag) -> Tag:
    for parent in image.parents:
        if isinstance(parent, Tag) and any(
            name == "bild" or name.startswith("pictable_")
            for name in (parent.get("class") or [])
        ):
            return parent
    assert isinstance(image.parent, Tag)
    return image.parent


def candidates(url: str, body: bytes, entities: list[dict]) -> Iterable[MediaCandidate]:
    owners = sorted(
        (entity["id"], page["id"])
        for entity in entities
        for page in entity["evidence"]
        if page["url"] == url
    )
    if not owners:
        return
    soup = parse_html(body)
    content = soup.select_one("div.content")
    if content is None:
        raise ValueError("Radartutorial media page is missing its content")
    images = content.select("img[src]")
    originals: dict[str, str] = {}
    for image in images:
        link = _container(image).select_one("a.lupe-r[href], a.lupe-l[href]")
        if link is not None:
            target = _url(url, str(link["href"]))
            if any(urlsplit(target).path.lower().endswith(s) for s in _FORMATS):
                originals[_url(url, str(image["src"]))] = target
    for image in images:
        target = _url(url, str(image["src"]))
        container = _container(image)
        caption_node = container.find(["figcaption", "p"], recursive=False)
        caption = text(caption_node) if caption_node else str(image.get("alt", ""))
        heading = image.find_previous(re.compile(r"^h[1-6]$"))
        section = text(heading) if heading else ""
        reason = ""
        if "/logos/" in urlsplit(target).path or image.find_parent(class_="logo"):
            reason = "manufacturer_logo"
        elif image.find_parent(["nav", "header", "footer"]) or image.find_parent(
            class_="werbung"
        ):
            reason = "navigation_image"
        link = image.find_parent("a", href=True)
        if link and re.search(
            r"/en/karte\d+\.en\.html$", urlsplit(_url(url, str(link["href"]))).path
        ):
            if _url(url, str(link["href"])) != url:
                reason = "cross_linked_entity"
        original = originals.get(target, target)
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
