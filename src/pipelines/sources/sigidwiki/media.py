"""Associate only the waterfall cell of each database row with its signal."""

import re
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup

from pipelines.media import MediaCandidate, MediaReference
from pipelines.model import evidence_id
from pipelines.sources.sigidwiki import DATABASE, ORIGIN, record_key, records


def original_url(value: str) -> str:
    url = urljoin(DATABASE, value)
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or parts.netloc != urlsplit(ORIGIN).netloc
        or parts.query
        or parts.fragment
        or not re.fullmatch(
            r"/images/(?:thumb/)?[0-9a-f]/[0-9a-f]{2}/[^/]+(?:/[^/]+)?", parts.path
        )
    ):
        raise ValueError("Invalid SigIDWiki waterfall URL")
    path = parts.path
    if path.startswith("/images/thumb/"):
        if len(path.split("/")) != 7:
            raise ValueError("Invalid MediaWiki thumbnail path")
        path = path.replace("/images/thumb/", "/images/", 1).rsplit("/", 1)[0]
    elif len(path.split("/")) != 5:
        raise ValueError("Invalid MediaWiki original path")
    return ORIGIN + path


def discover(url: str, body: bytes, entities: list[dict]) -> list[MediaCandidate]:
    if url != DATABASE or not entities:
        return []
    owners = {entity["id"]: entity for entity in entities}
    result = []
    for record in records(body):
        key = record_key(record["url"])
        identity = "sigidwiki:" + key
        if identity not in owners:
            raise ValueError("Waterfall has no retained signal record")
        page_id = evidence_id(DATABASE, key)
        if page_id not in {page["id"] for page in owners[identity]["evidence"]}:
            raise ValueError("Waterfall has no retained row evidence")
        row = BeautifulSoup(record["row_html"], "html.parser")
        cell = row.select("tr > td")[-1]
        for img in cell.select("img[src]"):
            target = original_url(str(img["src"]))
            caption = record["title"] + " — source waterfall example"
            reason = (
                ""
                if urlsplit(target)
                .path.lower()
                .endswith((".png", ".jpg", ".jpeg", ".gif", ".webp"))
                else "unsupported_image_format"
            )
            result.append(
                MediaCandidate(
                    url=target,
                    references=[
                        MediaReference(identity, page_id, caption, "Waterfall image")
                    ],
                    exclusion_reason=reason,
                    page_url=DATABASE,
                    caption=caption,
                    section="Waterfall image",
                )
            )
    return result
