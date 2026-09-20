import json
import socket
from pathlib import Path

import pytest

from pipelines.archive import Archive
from pipelines.build import build
from pipelines.distribution import collect, content_digest
from pipelines.model import evidence_id
from pipelines.sources.armyrecognition import CATALOG, ORIGIN, SEED, ArmyRecognition

URL = SEED + "/example-sensor"
BODY = (Path(__file__).parent / "fixtures/armyrecognition.html").read_bytes()
LISTING = f'''<main><h1>Air Defense Radars .</h1><h3 class="el-title"><a href="{URL}">Example sensor</a></h3><a href="/military-products/air/fighter">Fighters</a></main>'''.encode()


def source() -> ArmyRecognition:
    adapter = ArmyRecognition()
    adapter.minimum_entities = 1
    adapter.subjects = {
        "example-sensor": {"kind": "sensor", "aliases": ["EO-12", "Missing alias"]}
    }
    return adapter


def legacy(*, columns: bool = False) -> bytes:
    heading = '<tr bgcolor="#bfe9c2"><td>Country</td><td>Range</td></tr>'
    values = '<tr bgcolor="#e1f7e3"><td>Example country</td><td>? km maximum</td></tr>'
    if not columns:
        heading = '<tr><td bgcolor="#bfe9c2">Country</td></tr>'
        values = '<tr><td bgcolor="#e1f7e3">Example country</td></tr><tr><td bgcolor="#bfe9c2">Range</td></tr><tr><td bgcolor="#e1f7e3">? km maximum</td></tr>'
    return f'''<html><head><link rel="canonical" href="{URL}"></head><main><h1>Example sensor</h1><div>20 Sep, 2026 - 12:00</div><div class="content-article-template"><table><tr><td><p>The EO-12 is an optical sensor with day and night cameras. Identification is tentative. This article preserves configuration qualifications and unknown values, with the complete specification wording retained for further review.</p><a href="#specifications">Specifications</a><table>{heading}{values}<tr><td bgcolor="#bfe9c2">Weight</td></tr><tr><td bgcolor="#e1f7e3">100 kg<span style="color: #e1f7e3">a</span></td></tr><tr><td>Additional caveat outside paired rows.</td></tr></table><a href="/images/original.jpg"><img class="sigFreeImg" src="/cache/random-123.gif" alt="Diagram"></a></td></tr></table></div></main></html>'''.encode()


def test_query_parameters_select_category_not_path_and_no_scope_leakage() -> None:
    adapter = source()
    for query in ["task=view&id=139", "id=139&task=view"]:
        assert (
            adapter.normalize(ORIGIN + "/military-products/air/fighter?" + query)
            == SEED
        )
    assert adapter.normalize("http://armyrecognition.com" + CATALOG + "/#top") == SEED
    assert adapter.normalize(URL + "#spec") == URL
    for url in [
        ORIGIN + "/military-products/air/fighter",
        ORIGIN + "/military-products/air/fighter?task=view&id=140",
        ORIGIN + "/military-products/air/fighter?task=view&id=139&id=139",
        SEED + "?start=12",
        SEED + "?id=139&start=12",
        URL + "?task=edit",
        SEED + "/../other",
        SEED + "/%2e%2e/other",
        SEED + "/child/image.jpg",
        "https://example.org" + CATALOG,
        "https://user@www.armyrecognition.com" + CATALOG,
    ]:
        assert adapter.normalize(url) is None
    assert adapter.discover(SEED, LISTING) == [URL]
    assert adapter.discover(URL, BODY) == []
    assert adapter.extract(SEED, LISTING, []) == []


def test_catalog_pagination_and_layout_changes_fail_closed() -> None:
    for extra in [
        b'<a href="?start=12">2</a>',
        b'<ul class="uk-pagination"><li><a href="/next">Next</a></li></ul>',
    ]:
        with pytest.raises(ValueError, match="pagination"):
            source().discover(SEED, LISTING.replace(b"</main>", extra + b"</main>"))
    with pytest.raises(ValueError, match="category"):
        source().discover(SEED, LISTING.replace(b"Air Defense Radars", b"Fighters"))
    with pytest.raises(ValueError, match="no catalog members"):
        source().discover(SEED, LISTING.replace(b'class="el-title"', b'class="other"'))


def test_modern_full_evidence_and_subject_identity() -> None:
    entity = source().extract(URL, BODY, ["Example sensor"])[0]
    entity.validate()
    page = entity.evidence[0]
    assert entity.kind == "sensor" and entity.key == evidence_id(URL)
    assert entity.aliases == ["EO-12", "Example sensor"]
    assert "Other radar" not in entity.aliases
    for phrase in [
        "Identification is tentative",
        "configuration dependent",
        "Identification caveat",
        "September 20, 2026",
    ]:
        assert phrase in page.markdown and phrase in page.search_text
    for phrase in [
        "Unrelated",
        "More Products",
        "Back to top",
        "tracking data",
        "Global navigation",
    ]:
        assert phrase not in page.markdown and phrase not in page.search_text
    assert "Repetitive equipment keywords" in page.markdown
    assert "Repetitive equipment keywords" not in page.search_text
    assert ORIGIN + "/reference" in page.links
    assert ORIGIN + "/images/photo.jpg" in page.links
    assert "legal-information" in page.attribution and "Copyright" in page.attribution
    facts = {f.name: f for f in entity.facts}
    assert facts["Range radar detection"].raw == "? km maximum"
    assert facts["Range radar detection"].values == []
    assert all(f.evidence == URL for f in entity.facts)


@pytest.mark.parametrize("columns", [False, True])
def test_legacy_nested_tables_preserve_column_meanings_and_caveats(
    columns: bool,
) -> None:
    entity = source().extract(URL, legacy(columns=columns), ["Example sensor"])[0]
    facts = {f.name: f.raw for f in entity.facts}
    assert facts == {
        "Country": "Example country",
        "Range": "? km maximum",
        "Weight": "100 kg",
    }
    page = entity.evidence[0]
    assert "Additional caveat outside paired rows" in page.markdown
    assert "**Range:** ? km maximum" in page.markdown
    assert "/cache/random" not in page.markdown
    assert ORIGIN + "/images/original.jpg" in page.markdown


@pytest.mark.parametrize(
    "body",
    [
        BODY.replace(b'rel="canonical"', b'rel="missing"'),
        BODY.replace(b'id="spec"', b'id="missing"'),
        BODY.replace(b"Example sensor", b"Different item"),
        BODY.replace(
            b'class="el-content">? km maximum', b'class="missing">? km maximum'
        ),
        b"<html>Loading article</html>",
    ],
)
def test_incomplete_detail_fails_closed(body: bytes) -> None:
    with pytest.raises(ValueError):
        source().extract(URL, body, ["Example sensor"])


def test_membership_and_new_subject_require_review() -> None:
    with pytest.raises(ValueError, match="membership"):
        source().extract(URL, BODY, [])
    with pytest.raises(ValueError, match="Unreviewed"):
        ArmyRecognition().extract(URL, BODY, ["Example sensor"])


def test_optional_jsonld_and_ad_churn_do_not_change_identity_or_content() -> None:
    adapter = source()
    original = adapter.extract(URL, BODY, ["Example sensor"])[0].metadata(adapter.id)
    changed = (
        BODY.replace(b"999", b"1000")
        .replace(b"article/123", b"article/456")
        .replace(b"Unrelated ad", b"Rotated ad")
        .replace(b"click/42", b"click/43")
    )
    replacement = adapter.extract(URL, changed, ["Example sensor"])[0].metadata(
        adapter.id
    )
    assert content_digest([original]) == content_digest([replacement])


def test_offline_publication_keeps_exact_original_html(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter = source()
    archive = Archive(tmp_path / adapter.id / "archives/fixture")
    for url, body in [(SEED, LISTING), (URL, BODY)]:
        archive.add(url)
        archive.save(url, 200, body, "text/html", {})
    archive.mark_complete(adapter.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction attempted network access")

    monkeypatch.setattr(socket, "socket", forbidden)
    path = build(adapter, tmp_path, archive_id="fixture")
    entities, bodies = collect(tmp_path, [adapter.id])
    assert entities[0]["id"] == "armyrecognition:" + evidence_id(URL)
    assert list(bodies.values()) == [BODY]
    manifest = json.loads((path / "manifest.json").read_text())
    assert manifest["entities"] == 1 and manifest["evidence_pages"] == 1
