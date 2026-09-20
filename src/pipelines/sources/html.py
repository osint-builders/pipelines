from bs4 import Tag


def text(node: Tag) -> str:
    return " ".join(node.stripped_strings)
