import pytest
from bs4 import BeautifulSoup

from pipelines.sources.html import resolve_links


@pytest.mark.parametrize("encode_spaces", [False, True])
def test_resolve_links_preserves_content_and_source_spacing(
    encode_spaces: bool,
) -> None:
    content = BeautifulSoup(
        '<main><a href="#spec">Specifications</a>'
        '<img src="../photo one.jpg" data-src="original.jpg">'
        '<a href="//cdn.example.test/file">File</a>'
        '<a href="mailto:author@example.test">Author</a>'
        '<a href="javascript:alert(1)">Label</a>'
        '<img src="data:image/png;base64,AA==" alt="Diagram"></main>',
        "html.parser",
    )
    resolve_links(
        content, "https://example.test/equipment/item", encode_spaces=encode_spaces
    )
    anchors = content.find_all("a")
    assert [node.get("href") for node in anchors] == [
        "https://example.test/equipment/item#spec",
        "https://cdn.example.test/file",
        None,
        None,
    ]
    assert [node.get_text() for node in anchors] == [
        "Specifications",
        "File",
        "Author",
        "Label",
    ]
    images = content.find_all("img")
    assert (
        images[0]["src"]
        == "https://example.test/photo" + ("%20" if encode_spaces else " ") + "one.jpg"
    )
    assert images[0]["data-src"] == "original.jpg"
    assert "src" not in images[1].attrs
    assert images[1]["alt"] == "Diagram"
