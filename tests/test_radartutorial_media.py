from pipelines.sources.base import MediaSource
from pipelines.sources.radartutorial import Radartutorial

URL = "https://www.radartutorial.eu/19.kartei/02.surv/en/karte001.en.html"
OWNER = [{"id": "radartutorial:radar", "evidence": [{"id": "page", "url": URL}]}]


def test_originals_and_duplicate_print_previews_keep_caption_and_group() -> None:
    body = b"""<div class="content"><h2>Example radar</h2>
    <div class="pictable_c prn"><img src="../pic/radar-small.jpg"><p>Figure 1</p></div>
    <div class="pictable_c scr"><img src="../pic/radar-small.jpg">
    <a class="lupe-r" href="../pic/radar.jpg"></a><p>Figure 1: Radar at sea</p></div>
    <div class="logo"><img src="../../../logos/maker.png#maker"></div></div>"""
    source = Radartutorial()
    assert isinstance(source, MediaSource)
    rows = list(source.discover_media(URL, body, OWNER))
    originals = [
        row for row in rows if row.role == "original" and not row.exclusion_reason
    ]
    previews = [row for row in rows if row.role == "preview"]
    assert {row.url for row in originals} == {
        URL.rsplit("/en/", 1)[0] + "/pic/radar.jpg"
    }
    assert len(previews) == 2
    assert all(row.original_url == originals[0].url for row in previews)
    assert originals[-1].references[0].caption == "Figure 1: Radar at sea"
    assert originals[-1].references[0].section == "Example radar"
    assert rows[-1].exclusion_reason == "manufacturer_logo"
    assert rows[-1].references == []
    assert "#" not in rows[-1].url


def test_crosslinked_and_unsupported_media_are_not_associated() -> None:
    body = b"""<div class="content"><h2>Radar</h2>
    <a href="karte002.en.html"><img src="../pic/other.jpg"></a>
    <div class="bild"><img src="../pic/diagram.svg"></div>
    <div class="bild"><img src="https://outside.example/photo.jpg"></div></div>"""
    rows = list(Radartutorial().discover_media(URL, body, OWNER))
    assert [row.exclusion_reason for row in rows] == [
        "cross_linked_entity",
        "unsupported_image_format",
        "unsupported_media_origin",
    ]
    assert not rows[0].references
    assert rows[1].references[0].entity_id == "radartutorial:radar"
    assert list(Radartutorial().discover_media(URL, body, [])) == []
