import json
import re
from collections.abc import Iterator
from html import escape

from pipelines.model import Fact


def scalar(value: object, field: str) -> str:
    if value is None:
        return ""
    if not isinstance(value, str | int | float | bool):
        raise ValueError(f"Invalid ODIN {field}")
    return str(value).strip()


def sections(record: dict) -> list[dict]:
    value = record.get("sections", [])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except ValueError as exc:
            raise ValueError("Invalid ODIN sections JSON") from exc

    def validate(items: object, depth: int = 0) -> None:
        if not isinstance(items, list) or depth > 30:
            raise ValueError("Invalid ODIN section hierarchy")
        for section in items:
            if not isinstance(section, dict) or not isinstance(
                section.get("name"), str
            ):
                raise ValueError("Invalid ODIN section name")
            props = section.get("properties", [])
            if not isinstance(props, list):
                raise ValueError("Invalid ODIN section properties")
            for prop in props:
                if not isinstance(prop, dict) or not isinstance(prop.get("name"), str):
                    raise ValueError("Invalid ODIN property name")
                if "value" not in prop:
                    raise ValueError("Missing ODIN property value")
                scalar(prop["value"], "property value")
                scalar(prop.get("units", ""), "property units")
            validate(section.get("sections", []), depth + 1)

    validate(value)
    return value


def terms(record: dict, field: str) -> list[str]:
    items = record.get(field, [])
    if not isinstance(items, list):
        raise ValueError(f"Invalid ODIN {field} categories")
    values = []
    for item in items:
        if not isinstance(item, dict) or not item:
            raise ValueError(f"Invalid ODIN {field} category")
        for value in item.values():
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Invalid ODIN {field} category label")
            values.append(value.strip())
    return sorted(set(values))


def properties(record: dict) -> Iterator[tuple[tuple[str, ...], dict]]:
    def visit(
        items: list[dict], path: tuple[str, ...]
    ) -> Iterator[tuple[tuple[str, ...], dict]]:
        for section in items:
            current = (*path, section["name"].strip() or "Unnamed section")
            for prop in section.get("properties", []):
                yield current, prop
            yield from visit(section.get("sections", []), current)

    yield from visit(sections(record), ())


def property_value(prop: dict) -> str:
    value = scalar(prop["value"], "property value")
    units = scalar(prop.get("units", ""), "property units")
    return " ".join(part for part in (value, units) if part)


def metadata(record: dict) -> list[tuple[str, str]]:
    result = []
    for field, name in (
        ("domain", "Domain"),
        ("origin", "Origin"),
        ("proliferation", "Proliferation"),
    ):
        values = terms(record, field)
        if values:
            result.append((name, "; ".join(values)))
    for field, name in (
        ("dateOfIntroduction", "Date of introduction"),
        ("disname", "DIS name"),
        ("disstring", "DIS enumeration"),
        ("authorModDate", "Author modified"),
        ("publishDate", "Published"),
        ("modDate", "Modified"),
        ("creationDate", "Created"),
    ):
        if value := scalar(record.get(field, ""), field):
            result.append((name, value))
    return result


def record_facts(record: dict, canonical: str) -> list[Fact]:
    result = [Fact(name, value, canonical) for name, value in metadata(record)]
    for path, prop in properties(record):
        name = " / ".join((*path, prop["name"].strip() or "Unnamed property"))
        result.append(Fact(name, property_value(prop), canonical))
    return result


def aliases(record: dict) -> list[str]:
    result = {scalar(record.get("name", ""), "name")}
    disname = scalar(record.get("disname", ""), "disname")
    if disname.lower() not in {"", "unknown", "ina", "n/a", "none"}:
        result.add(disname)
    for _, prop in properties(record):
        if re.fullmatch(
            r"(?:alternate|alternative) designation(?:\(s\)|s)?",
            prop["name"].strip(),
            re.I,
        ):
            for value in re.split(r"[;\n]", scalar(prop["value"], "alias")):
                if value.strip().lower() not in {"", "unknown", "ina", "n/a", "none"}:
                    result.add(value.strip())
    return sorted(result - {""})


def render(record: dict, *, include_images: bool = True) -> str:
    def value_html(value: str) -> str:
        return escape(value).replace("\r\n", "\n").replace("\n", "<br>\n")

    def render_sections(items: list[dict], depth: int = 2) -> str:
        result = []
        for section in items:
            level = min(depth, 6)
            result.append(
                f"<section><h{level}>{escape(section['name'].strip())}</h{level}>"
            )
            if props := section.get("properties", []):
                result.append("<dl>")
                for prop in props:
                    result.append(
                        f"<dt>{escape(prop['name'].strip())}</dt><dd>{value_html(property_value(prop))}</dd>"
                    )
                result.append("</dl>")
            result.append(render_sections(section.get("sections", []), depth + 1))
            result.append("</section>")
        return "".join(result)

    title = scalar(record.get("name") or record.get("title"), "title")
    details = "".join(
        f"<dt>{escape(name)}</dt><dd>{value_html(value)}</dd>"
        for name, value in metadata(record)
    )
    notes = scalar(record.get("notes", ""), "notes")
    images = ""
    if include_images:
        from pipelines.sources.odin.media import image_url, record_images

        figures = []
        for target, caption, context in record_images(record):
            url = escape(image_url(target), quote=True)
            label = escape(caption, quote=True)
            figures.append(
                f'<figure><a href="{url}"><img src="{url}" alt="{label}"></a>'
                f"<figcaption>{escape(context)}: {label}</figcaption></figure>"
            )
        if figures:
            images = (
                '<section class="source-images"><h2>Images</h2>'
                + "".join(figures)
                + "</section>"
            )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        f"<title>{escape(title)}</title></head><body><main><h1>{escape(title)}</h1>"
        f"<dl>{details}</dl>"
        + (
            f"<section><h2>Notes</h2><p>{value_html(notes)}</p></section>"
            if notes
            else ""
        )
        + render_sections(sections(record))
        + images
        + "</main></body></html>"
    )
