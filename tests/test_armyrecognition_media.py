from pipelines.sources.armyrecognition import ArmyRecognition
from pipelines.sources.base import MediaSource

URL = "https://www.armyrecognition.com/military-products/army/radars/air-defense-radars/example"
OWNER = [{"id": "armyrecognition:radar", "evidence": [{"id": "page", "url": URL}]}]


def test_legacy_gallery_original_and_placeholder_keep_one_group() -> None:
    body = b"""<main><div class="content-article-template"><h2>Photos</h2>
    <a class="sigFreeLink" href="/images/radar.jpg" data-thumb="/cache/radar.jpg">
    <img class="sigFreeImg" src="/plugins/transparent.gif" alt="Radar front"></a>
    </div><a href="/component/banners/click/1"><img src="/images/ad.jpg"></a></main>"""
    source = ArmyRecognition()
    assert isinstance(source, MediaSource)
    rows = list(source.discover_media(URL, body, OWNER))
    assert [row.exclusion_reason for row in rows] == [
        "",
        "transparent_placeholder",
        "",
        "advertisement",
    ]
    assert rows[0].url.endswith("/images/radar.jpg")
    assert rows[2].original_url == rows[0].url
    assert rows[2].references[0].caption == "Radar front"
    assert rows[2].references[0].section == "Photos"
    assert rows[-1].references == []


def test_current_sections_include_hero_and_exclude_related_cards() -> None:
    body = b"""<main><div class="margin-image-full"><img src="/media/hero.jpg"></div>
    <div id="photos"><h2>Gallery</h2><a href="/images/detail.jpg"><picture>
    <source srcset="/cache/small.webp 640w, /cache/large.webp 1200w">
    <img src="/cache/large.jpg"></picture></a></div>
    <a href="/military-products/army/radars/other"><img src="/images/other.jpg"></a>
    <div id="details"><img src="/images/drawing.svg#joomlaImage://local"></div></main>"""
    rows = list(ArmyRecognition().discover_media(URL, body, OWNER))
    assert rows[0].references and rows[0].url.endswith("/media/hero.jpg")
    assert sum(row.exclusion_reason == "redundant_preview" for row in rows) == 2
    assert rows[-2].exclusion_reason == "cross_linked_entity"
    assert rows[-2].references == []
    assert rows[-1].exclusion_reason == "unsupported_image_format"
    assert "#" not in rows[-1].url
    assert list(ArmyRecognition().discover_media(URL, body, [])) == []
