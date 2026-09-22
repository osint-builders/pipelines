import json
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.model import Entity, Evidence, evidence_id
from pipelines.sources.html import text


def reviews() -> list[dict]:
    return json.loads(
        Path(__file__).with_name("image_reviews.json").read_text(encoding="utf-8")
    )


def validate_page(url: str, body: bytes, matches: list[dict]) -> None:
    soup = BeautifulSoup(body, "html.parser")
    visible = " ".join(text(soup).split())
    for row in matches:
        if row["page_url"] != url:
            continue
        if not row["quote"] or row["quote"] not in visible:
            raise ValueError(f"Reviewed radar evidence changed: {row['model']}")
        images = {
            urljoin(url, str(node.get(attribute, "")))
            for node in soup.select("img, video[poster]")
            for attribute in ("src", "data-src", "data-original", "poster")
            if node.get(attribute)
        }
        images.update(
            urljoin(url, str(node["href"])) for node in soup.select("a[href]:has(img)")
        )
        for image in tuple(images):
            parts = urlsplit(image)
            if parts.path == "/_next/image":
                images.update(
                    urljoin(url, value)
                    for value in parse_qs(parts.query).get("url", [])
                )
        if row["image_url"] not in images:
            raise ValueError(f"Reviewed radar image changed: {row['model']}")


def extract(
    url: str, body: bytes, matches: list[dict], catalog: dict[str, Entity]
) -> list[Entity]:
    from pipelines.sources.cambridgepixel import identity

    validate_page(url, body, matches)
    result = []
    for row in matches:
        if row["page_url"] != url:
            continue
        key = identity(row["manufacturer"], row["model"])
        base = catalog[key]
        caption = row["caption"]
        markdown = f"{row['quote']}\n\n![{caption}]({row['image_url']})\n\nImage search: {row['search_url']}\n\nMatch review: {row['notes']}"
        rendered = f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(base.title)}</title></head><body><h1>{escape(base.title)}</h1><p>{escape(row["quote"])}</p><figure><img src="{escape(row["image_url"], quote=True)}" alt="{escape(caption, quote=True)}"><figcaption>{escape(caption)}</figcaption></figure><p>{escape(row["notes"])}</p><a href="{escape(url, quote=True)}">Image source</a></body></html>'
        result.append(
            Entity(
                key=key,
                title=base.title,
                kind=base.kind,
                evidence=[
                    Evidence(
                        url=url,
                        title=base.title,
                        markdown=markdown,
                        links=[row["image_url"], row["search_url"]],
                        attribution=f"Image source: {url}\nCapture method: {row.get('capture_method', 'http-response')}",
                        rendered_html=rendered,
                        record_id=key,
                        records=[row],
                        search_text=f"{base.title}\n{row['quote']}\n{caption}",
                    )
                ],
            )
        )
    return result


def discover(
    url: str, body: bytes, entities: list[dict], matches: list[dict]
) -> list[MediaCandidate]:
    from pipelines.sources.cambridgepixel import identity

    validate_page(url, body, matches)
    owners = {
        (e["id"], p["id"]) for e in entities for p in e["evidence"] if p["url"] == url
    }
    result = []
    for row in matches:
        if row["page_url"] != url:
            continue
        key = identity(row["manufacturer"], row["model"])
        owner = ("cambridgepixel:" + key, evidence_id(url, key))
        if owner not in owners:
            raise ValueError("Reviewed radar image has no captured entity evidence")
        result.append(
            MediaCandidate(
                row["image_url"],
                [
                    MediaReference(
                        *owner,
                        caption=row["caption"],
                        section="Reviewed radar image",
                        ambiguous=row.get("ambiguous", False),
                        association="reviewed_family_context"
                        if row.get("ambiguous")
                        else "reviewed_model_match",
                    )
                ],
                page_url=url,
                caption=row["caption"],
                section="Reviewed radar image",
            )
        )
    return result


def origins(matches: list[dict]) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                f"{p.scheme}://{p.netloc}"
                for row in matches
                for p in [urlsplit(row["image_url"])]
            }
        )
    )
