from pipelines.sources.base import MediaSource
from pipelines.sources.russianforces import RussianForces

URL = "https://russianforces.org/blog/2026/05/exercise_of_strategic_forces_h.shtml"


def owners() -> list[dict]:
    return [
        {
            "id": f"russianforces:{key}",
            "title": title,
            "aliases": [title],
            "evidence": [{"id": "article", "url": URL}],
        }
        for key, title in [
            ("yars", "Yars"),
            ("kinzhal", "Kinzhal"),
            ("voronezh-dm", "Voronezh-DM"),
            ("voronezh-dm1", "Voronezh-DM1"),
        ]
    ]


def test_dated_filename_association_is_narrow_and_explicitly_uncertain() -> None:
    body = b"""<div class="entry-asset" id="entry-1"><h1>Exercise</h1><div class="asset-content">
    <p><a href="/20260521_Yars.png"><img src="/assets_c/yars-small.png" alt="20260521_Yars.png"></a>Yars and Kinzhal took part.</p>
    <p><img src="/map.png" alt="20260521_map.png"></p></div></div>
    <aside><img src="/navigation.png" alt="Yars"></aside>"""
    source = RussianForces()
    assert isinstance(source, MediaSource)
    rows = list(source.discover_media(URL, body, owners()))
    assert [ref.entity_id for ref in rows[0].references] == ["russianforces:yars"]
    assert rows[0].references[0].association == "source_filename"
    assert rows[0].references[0].ambiguous
    assert rows[1].original_url == rows[0].url
    assert rows[2].references == [] and rows[2].exclusion_reason == ""
    assert rows[3].exclusion_reason == "navigation_image" and not rows[3].references


def test_source_caption_distinguishes_variants_and_shared_subjects() -> None:
    body = b"""<div class="entry-asset" id="entry-1"><div class="asset-content">
    <figure><img src="/radar.jpg"><figcaption>Voronezh-DM1 radar</figcaption></figure>
    <figure><img src="/systems.jpg"><figcaption>Yars and Kinzhal</figcaption></figure>
    <img src="/diagram.svg" alt="Yars diagram"></div></div>"""
    rows = list(RussianForces().discover_media(URL, body, owners()))
    assert [ref.entity_id for ref in rows[0].references] == [
        "russianforces:voronezh-dm1"
    ]
    assert not rows[0].references[0].ambiguous
    assert all(ref.ambiguous for ref in rows[1].references)
    assert len(rows[1].references) == 2
    assert rows[-1].exclusion_reason == "unsupported_image_format"
    assert list(RussianForces().discover_media(URL, body, [])) == []
