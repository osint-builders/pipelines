"""Render the publisher's rich-text blocks without losing tables or source wording."""

from html import escape
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def heading(value: str, level: int) -> str:
    level = min(level, 6)
    return f"<h{level}>{escape(value)}</h{level}>" if value else ""


def table(value: dict) -> str:
    rows = value.get("data")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Missing Military Periscope table rows")
    choice = value.get("table_header_choice", "neither")
    if choice not in {"neither", "row", "column", "both"}:
        raise ValueError("Unknown Military Periscope table header layout")
    result = []
    for row_index, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError("Invalid Military Periscope table row")
        cells = []
        for col_index, cell in enumerate(row):
            if cell is not None and not isinstance(cell, str):
                raise ValueError("Invalid Military Periscope table cell")
            tag = (
                "th"
                if (row_index == 0 and choice in {"row", "both"})
                or (col_index == 0 and choice in {"column", "both"})
                else "td"
            )
            cells.append(f"<{tag}>{cell or ''}</{tag}>")
        result.append("<tr>" + "".join(cells) + "</tr>")
    caption = value.get("caption", "")
    return (
        "<table>"
        + (f"<caption>{escape(caption)}</caption>" if caption else "")
        + "".join(result)
        + "</table>"
    )


def media(value: dict) -> str:
    path = value.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Missing Military Periscope media link")
    label = value.get("title") or path.rsplit("/", 1)[-1]
    credit = value.get("source") or ""
    caption = value.get("caption") or ""
    return f'<figure><a href="{escape(path, quote=True)}">{escape(label)}</a><figcaption>{escape(caption)} {escape(credit)}</figcaption></figure>'


def render(blocks: list, labels: dict | None = None, level: int = 2) -> str:
    labels = labels or {}
    result = []
    for block in blocks:
        if (
            not isinstance(block, dict)
            or not isinstance(block.get("type"), str)
            or "value" not in block
        ):
            raise ValueError("Invalid Military Periscope content block")
        kind, value = block["type"], block["value"]
        if kind in {"content", "paragraph"} and isinstance(value, str):
            result.append(value)
        elif kind == "table" and isinstance(value, dict):
            result.append(table(value))
        elif kind == "image" and isinstance(value, dict):
            result.append(media(value))
        elif kind == "images" and isinstance(value, list):
            for item in value:
                result.append(media(item["image"]))
        elif kind in {"section", "subsection", "styled_block"} and isinstance(
            value, dict
        ):
            title = value.get("header") or value.get("style", "")
            result.append(heading(title, level))
            result.append(heading(value.get("subheader", ""), level + 1))
            result.append(render(value["body"], labels, level + 1))
        elif isinstance(value, list):
            # Named weapon/organization sections consist of nested typed blocks.
            result.append(
                heading(labels.get(kind, kind.replace("_", " ").title()), level)
            )
            result.append(render(value, labels, level + 1))
        else:
            raise ValueError(f"Unsupported Military Periscope content block: {kind}")
    return "\n".join(result)


def document(fragment: str, canonical: str, title: str) -> BeautifulSoup:
    soup = BeautifulSoup(fragment, "html.parser")
    for node in soup.select("script, style, form, iframe"):
        node.decompose()
    for node in soup.find_all(True):
        for attr in list(node.attrs):
            if attr.lower().startswith("on"):
                del node[attr]
    for node in soup.select("a[href], img[src]"):
        attr = "href" if node.name == "a" else "src"
        target = urljoin(canonical, str(node[attr]))
        if urlsplit(target).scheme not in {"http", "https", "mailto"}:
            del node[attr]
        else:
            node[attr] = target
    return BeautifulSoup(
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f'<title>{escape(title)}</title><link rel="canonical" href="{escape(canonical, quote=True)}">'
        f"</head><body>{soup}</body></html>",
        "html.parser",
    )
