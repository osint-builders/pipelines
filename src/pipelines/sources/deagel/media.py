from collections import defaultdict
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.sources.deagel import ORIGIN, VARIANT, content
from pipelines.sources.html import text


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    page = content(body)
    owners = {
        entity["id"]: evidence["id"]
        for entity in entities
        for evidence in entity["evidence"]
        if evidence["url"] == url
    }
    sections: dict[int, tuple[str, str]] = {}
    by_url: dict[str, set[str]] = defaultdict(set)
    family_id = urlsplit(url).path.rsplit("/", 1)[-1]
    for anchor in page.find_all("a", id=VARIANT):
        heading = anchor.find_next_sibling("h1")
        variant = heading.find_next_sibling("div") if heading else None
        if variant is None or heading is None:
            raise ValueError("Missing Deagel media variant section")
        owner = f"deagel:{family_id}-{anchor['id']}"
        for image in variant.select("img[src]"):
            sections[id(image)] = (owner, text(heading))
            by_url[urljoin(ORIGIN + "/", str(image["src"]))].add(owner)
    gallery_images = {}
    gallery_captions = {}
    for heading in page.find_all("h4"):
        if text(heading) != "Photo Gallery":
            continue
        gallery = heading.find_next_sibling("div")
        if gallery:
            for image in gallery.select("a[href] img[src]"):
                link = image.find_parent("a")
                label = link.select_one(".card-text") if link else None
                caption = text(label) if label else str(image.get("alt", ""))
                gallery_images[id(image)] = caption
                gallery_captions[urljoin(ORIGIN + "/", str(image["src"]))] = caption
    result = []
    soup = BeautifulSoup(body, "html.parser")
    for image in soup.select("img[src]"):
        target = urljoin(ORIGIN + "/", str(image["src"]))
        if urlsplit(target).path.startswith("/img/"):
            result.append(
                MediaCandidate(
                    target,
                    exclusion_reason="country_flag"
                    if "/flags/" in target
                    else "navigation_image",
                    page_url=url,
                    caption=str(image.get("alt", "")),
                    section="Page navigation",
                )
            )
    for image in page.select("img[src]"):
        target = urljoin(ORIGIN + "/", str(image["src"]))
        parts = urlsplit(target)
        if parts.path.startswith("/img/"):
            continue
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        caption = str(image.get("alt", "")) or gallery_captions.get(target, "")
        owner_ids = set()
        section = "Related content"
        reason = "related_news_image"
        if id(image) in sections:
            owner, title = sections[id(image)]
            owner_ids = {owner}
            section = title
            reason = ""
        elif id(image) in gallery_images:
            section = "Photo Gallery"
            owner_ids = by_url.get(target, set()) & owners.keys()
            if not owner_ids and len(owners) == 1:
                owner_ids = set(owners)
            reason = "" if owner_ids else "ambiguous_variant_image"
        references = [
            MediaReference(
                owner, owners[owner], caption, section, ambiguous=len(owner_ids) > 1
            )
            for owner in sorted(owner_ids)
            if owner in owners
        ]
        if not reason:
            if (
                parts.scheme != "https"
                or parts.netloc != "www.deagel.com"
                or not parts.path.startswith("/library/")
            ):
                reason = "outside_media_scope"
            elif not parts.path.lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                reason = "unsupported_image_format"
        preview = parts.path.startswith("/library/sm/")
        result.append(
            MediaCandidate(
                target,
                references,
                reason,
                role="preview" if preview else "original",
                original_url=target if preview else "",
                page_url=url,
                caption=caption,
                section=section,
            )
        )
    return result
