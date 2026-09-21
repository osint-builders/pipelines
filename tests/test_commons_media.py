import pytest
from bs4 import BeautifulSoup
from test_commons import BODY, FILE_URL, prepared

from pipelines.sources.commons import Commons

ORIGINAL = "https://upload.wikimedia.org/example.jpg"
THUMB = "https://thumb.wikimedia.org/wikipedia/commons/thumb/a/ab/Example.jpg/"


def page(
    *,
    previews: tuple[int, ...] = (330, 960, 1280, 1920),
    info: str = "",
    width: int = 2400,
    height: int = 1800,
) -> bytes:
    image = f'<img src="{THUMB}{previews[0]}px-Example.jpg?_=capture" srcset="{THUMB}{previews[-1]}px-Example.jpg?_=capture 2x" width="800" data-file-width="{width}" data-file-height="{height}">'
    links = "".join(
        f'<a class="mw-thumbnail-link" href="{THUMB}{size}px-Example.jpg?utm_content=thumbnail">{size:,} × 700 pixels</a>'
        for size in previews
    )
    full_image = f'<div class="fullImageLink" id="file"><a href="{ORIGINAL}">{image}</a>{links}<a class="mw-thumbnail-link" href="{ORIGINAL}?utm_content=thumbnail_unscaled">2,400 × 1,800 pixels</a></div>'
    return BODY.replace(
        b'<div id="mw-content-text">',
        b'<div id="mw-content-text">' + full_image.encode(),
    ).replace(
        b"Original file</a></div>",
        f'Original file</a><span class="fileInfo">{info}</span></div>'.encode(),
    )


def owners(body: bytes) -> list[dict]:
    source, _ = prepared()
    return [entity.metadata(source.id) for entity in source.extract(FILE_URL, body, [])]


def test_file_media_retains_all_owners_and_selects_one_bounded_preview() -> None:
    body = page()
    source = Commons()
    candidates = list(source.discover_media(FILE_URL, body, owners(body)))
    original = next(item for item in candidates if item.role == "original")
    previews = [item for item in candidates if item.role == "preview"]
    assert original.url == ORIGINAL and original.exclusion_reason == ""
    assert len(previews) == 7
    selected = [item for item in previews if not item.exclusion_reason]
    assert [item.url for item in selected] == [
        THUMB + "1280px-Example.jpg?utm_content=thumbnail"
    ]
    assert all(item.original_url == ORIGINAL for item in previews)
    assert {item.exclusion_reason for item in previews if item not in selected} == {
        "redundant_preview"
    }
    for item in candidates:
        assert item.page_url == FILE_URL
        assert item.section == "Description"
        assert "Identification is tentative" in item.caption
        assert "Исходное описание" in item.caption
        assert {reference.entity_id for reference in item.references} == {
            "commons:3",
            "commons:5",
        }
        assert all(
            reference.ambiguous and reference.caption == item.caption
            for reference in item.references
        )


def test_smallest_preview_is_selected_when_all_exceed_width_budget() -> None:
    body = page(previews=(1920, 3840))
    candidates = Commons().discover_media(FILE_URL, body, owners(body))
    selected = [
        item
        for item in candidates
        if item.role == "preview" and not item.exclusion_reason
    ]
    assert len(selected) == 1 and "1920px-" in selected[0].url


def test_navigation_and_description_illustrations_are_not_discovered() -> None:
    body = (
        page()
        .replace(
            b"</body>",
            b'<img src="https://upload.wikimedia.org/logo.png"><div id="mw-navigation"><img src="https://upload.wikimedia.org/unrelated.png"></div></body>',
        )
        .replace(
            b"<h2>Summary</h2>",
            b'<h2>Summary</h2><img src="https://upload.wikimedia.org/other.jpg">',
        )
    )
    candidates = list(Commons().discover_media(FILE_URL, body, owners(body)))
    assert not any(
        item.url.endswith(("logo.png", "unrelated.png", "other.jpg"))
        for item in candidates
    )
    assert (
        list(
            Commons().discover_media(
                "https://commons.wikimedia.org/wiki/Category:Example", body, []
            )
        )
        == []
    )


def test_unassociated_file_keeps_context_and_does_not_guess_owner() -> None:
    candidates = list(Commons().discover_media(FILE_URL, page(), []))
    assert all(not item.references for item in candidates)
    assert all("Identification is tentative" in item.caption for item in candidates)
    body = page()
    entity = owners(body)[0]
    candidates = list(Commons().discover_media(FILE_URL, body, [entity]))
    assert all(
        not reference.ambiguous for item in candidates for reference in item.references
    )


@pytest.mark.parametrize(
    "info,width,height,reason",
    [
        ("file size: 43.01 MB", 2400, 1800, "declared_image_too_large"),
        ("file size: 20.93 MB", 2400, 1800, "declared_image_too_large"),
        ("file size: 21 MiB", 2400, 1800, "declared_image_too_large"),
        ("file size: 700 KB", 10000, 10000, "declared_image_too_many_pixels"),
        (
            'MIME type: <span class="mime-type">image/tiff</span>',
            2400,
            1800,
            "unsupported_image_format",
        ),
    ],
)
def test_declared_original_limits_are_explicit_and_keep_preview(
    info: str, width: int, height: int, reason: str
) -> None:
    body = page(info=info, width=width, height=height)
    candidates = list(Commons().discover_media(FILE_URL, body, owners(body)))
    assert candidates[0].role == "original" and candidates[0].exclusion_reason == reason
    assert (
        sum(item.role == "preview" and not item.exclusion_reason for item in candidates)
        == 1
    )


def test_svg_and_video_originals_keep_raster_previews() -> None:
    body = page().replace(ORIGINAL.encode(), ORIGINAL.replace(".jpg", ".svg").encode())
    candidates = list(Commons().discover_media(FILE_URL, body, owners(body)))
    assert candidates[0].exclusion_reason == "unsupported_image_format"
    assert any(
        item.role == "preview" and not item.exclusion_reason for item in candidates
    )
    soup = BeautifulSoup(page(), "html.parser")
    container = soup.select_one("#file")
    assert container
    container.clear()
    container.append(
        BeautifulSoup(
            f'<video poster="{THUMB}960px-video.webm.jpg" width="800"></video>',
            "html.parser",
        )
    )
    body = str(soup).replace(ORIGINAL, ORIGINAL.replace(".jpg", ".webm")).encode()
    candidates = list(Commons().discover_media(FILE_URL, body, owners(body)))
    assert candidates[0].exclusion_reason == "unsupported_image_format"
    assert candidates[1].role == "preview" and candidates[1].exclusion_reason == ""


def test_media_discovery_keeps_existing_text_and_raw_evidence_unchanged() -> None:
    source, _ = prepared()
    body = page()
    before = source.extract(FILE_URL, body, [])
    list(
        source.discover_media(
            FILE_URL, body, [entity.metadata(source.id) for entity in before]
        )
    )
    assert source.extract(FILE_URL, body, []) == before


def test_missing_original_fails_and_outside_origins_are_explicitly_excluded() -> None:
    with pytest.raises(ValueError, match="original media URL"):
        list(Commons().discover_media(FILE_URL, b"<html></html>", []))
    body = BODY.replace(ORIGINAL.encode(), b"https://unrelated.example/image.jpg")
    candidates = list(Commons().discover_media(FILE_URL, body, []))
    assert candidates[0].exclusion_reason == "unsupported_media_origin"


def test_srcset_retains_commas_inside_filenames() -> None:
    first = THUMB + "640px-Example,_one.jpg"
    second = THUMB + "1280px-Example,_one.jpg"
    body = BODY.replace(
        b'<div id="mw-content-text">',
        f'<div id="mw-content-text"><div class="fullImageLink" id="file"><img src="{first}" srcset="{first} 1x, {second} 2x"></div>'.encode(),
    )
    candidates = list(Commons().discover_media(FILE_URL, body, owners(body)))
    assert {item.url for item in candidates} == {ORIGINAL, first, second}
