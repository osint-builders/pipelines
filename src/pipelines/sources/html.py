from urllib.parse import urljoin, urlsplit

from bs4 import Tag


def text(node: Tag) -> str:
    return " ".join(node.stripped_strings)


def resolve_links(content: Tag, url: str, *, encode_spaces: bool = False) -> None:
    """Resolve retained href/src links and remove non-HTTP schemes."""
    for node in content.select("[href], [src]"):
        for attr in ("href", "src"):
            if attr in node.attrs:
                target = urljoin(url, str(node[attr]))
                if encode_spaces:
                    target = target.replace(" ", "%20")
                if urlsplit(target).scheme in {"http", "https"}:
                    node[attr] = target
                else:
                    del node[attr]
