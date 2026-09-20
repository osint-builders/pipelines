"""Read metadata shared by archived MediaWiki HTML sources."""

import json
import re

from bs4 import BeautifulSoup, Tag


def text(node: Tag) -> str:
    return " ".join(node.stripped_strings)


def config(soup: BeautifulSoup, name: str) -> object:
    for script in soup.find_all("script"):
        match = re.search(r'"' + re.escape(name) + r'"\s*:\s*', script.text)
        if match:
            return json.JSONDecoder().raw_decode(script.text[match.end() :])[0]
    raise ValueError(f"Missing MediaWiki metadata: {name}")
