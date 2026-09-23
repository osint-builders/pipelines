import base64
import json
import re
from html import escape
from pathlib import Path
from urllib.parse import parse_qs, quote, urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.model import Entity, Evidence, evidence_id
from pipelines.sources.html import text


def reviews() -> list[dict]:
    return json.loads(
        Path(__file__).with_name("image_reviews.json").read_text(encoding="utf-8")
    )


def _image_url(base: str, value: str) -> str:
    parts = urlsplit(value)
    if (
        not parts.scheme
        and not parts.netloc
        and parts.path
        and not parts.path.startswith("/")
    ):
        path = urlsplit(base).path.rsplit("/", 1)[0] + "/" + parts.path
        value = parts._replace(path=path).geturl()
    return urljoin(base, value)


def validate_page(url: str, body: bytes, matches: list[dict]) -> None:
    pdf_matches = [
        row for row in matches if row["page_url"] == url and "pdf_image" in row
    ]
    if pdf_matches:
        from pipelines.sources.cambridgepixel.pdf_image import extract as pdf_image

        for row in pdf_matches:
            pdf_image(body, row)
        if len(pdf_matches) != sum(row["page_url"] == url for row in matches):
            raise ValueError("Mixed PDF and HTML radar image reviews")
        return
    soup = BeautifulSoup(body, "html.parser")
    base = soup.select_one("base[href]")
    image_base = urljoin(url, str(base["href"])) if base else url
    visible = " ".join(text(soup).split())
    for row in matches:
        if row["page_url"] != url:
            continue
        if not row["quote"] or row["quote"] not in visible:
            raise ValueError(f"Reviewed radar evidence changed: {row['model']}")
        images = {
            _image_url(image_base, str(node.get(attribute, "")))
            for node in soup.select("img, video[poster]")
            for attribute in (
                "src",
                "data-src",
                "data-original",
                "data-src-url-d",
                "poster",
            )
            if node.get(attribute)
        }
        images.update(
            _image_url(image_base, str(node["href"]))
            for node in soup.select(
                'a[href]:has(img), a[href]:has([role="img"]), img + a[href][title], '
                'a[href][type^="image/"]'
            )
        )
        images.update(
            _image_url(image_base, str(node["data-thumbnail"]))
            for node in soup.select('[role="img"][data-thumbnail]')
        )
        css_property = row.get("image_css_property")
        if css_property is not None and not re.fullmatch(
            r"--[a-z][a-z0-9-]*", css_property
        ):
            raise ValueError("Invalid reviewed image CSS property")
        css_properties = "background-image|background"
        if css_property:
            css_properties += "|" + re.escape(css_property)
        for node in soup.select("[style]"):
            match = re.search(
                rf"(?:^|;)\s*(?:{css_properties})\s*:\s*url\(\s*(['\"]?)(.*?)\1\s*\)",
                str(node["style"]),
                flags=re.IGNORECASE,
            )
            if match:
                images.add(_image_url(image_base, match.group(2)))
        for node in soup.select("img[srcset], source[srcset]"):
            images.update(
                _image_url(image_base, match.group(1))
                for match in re.finditer(
                    r"(\S+?)(?:\s+\d+(?:\.\d+)?[wx])?(?:\s*,\s*|$)",
                    str(node["srcset"]),
                )
            )
        for image in tuple(images):
            parts = urlsplit(image)
            if parts.path == "/_next/image":
                images.update(
                    _image_url(image_base, value)
                    for value in parse_qs(parts.query).get("url", [])
                )
        safe = ":/?#[]@!$&'()*+,;=%"
        if quote(row["image_url"], safe=safe) not in {
            quote(image, safe=safe) for image in images
        }:
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
        display_url = row["image_url"]
        if "pdf_image" in row:
            from pipelines.sources.cambridgepixel.pdf_image import extract as pdf_image

            image_body, mime = pdf_image(body, row)
            display_url = f"data:{mime};base64,{base64.b64encode(image_body).decode()}"
        image_marker = "" if "pdf_image" in row else "!"
        markdown = f"{row['quote']}\n\n{image_marker}[{caption}]({row['image_url']})\n\nImage search: {row['search_url']}\n\nMatch review: {row['notes']}"
        rendered = f'<!doctype html><html><head><meta charset="utf-8"><title>{escape(base.title)}</title></head><body><h1>{escape(base.title)}</h1><p>{escape(row["quote"])}</p><figure><img src="{escape(display_url, quote=True)}" alt="{escape(caption, quote=True)}"><figcaption>{escape(caption)}</figcaption></figure><p>{escape(row["notes"])}</p><a href="{escape(url, quote=True)}">Image source</a></body></html>'
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
        embedded_body, embedded_type = None, ""
        if "pdf_image" in row:
            from pipelines.sources.cambridgepixel.pdf_image import extract as pdf_image

            embedded_body, embedded_type = pdf_image(body, row)
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
                role="preview" if embedded_body is not None else "original",
                original_url=url if embedded_body is not None else "",
                embedded_body=embedded_body,
                embedded_content_type=embedded_type,
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
