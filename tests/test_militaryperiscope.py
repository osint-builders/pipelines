import base64
import json
import socket
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from scrapy import Request
from scrapy.crawler import Crawler
from scrapy.exceptions import CloseSpider, IgnoreRequest
from scrapy.http import Response

from pipelines.archive import Archive
from pipelines.build import publish
from pipelines.crawl import ArchiveSpider, ScopeMiddleware
from pipelines.distribution import collect_artifacts
from pipelines.model import EntityKind
from pipelines.sources.militaryperiscope import (
    API,
    ORIGIN,
    TOC,
    TRIAL,
    MilitaryPeriscope,
    catalog,
    entity_kind,
    page_url,
)
from pipelines.sources.militaryperiscope_content import render

WEAPON = "/weapons/aircraft/aerostats/example/overview/"
COUNTRY = "/armedforces/africa/example/overview/"
CHAPTER = "/armedforces/africa/example/forcestructures/"
COMPANY = "/defense-companies/example/"
GROUP = "/militant-organizations/africa/example/"


def encoded(value: dict) -> bytes:
    return json.dumps(value).encode()


def toc() -> dict:
    sections = []
    for i, (path, kind, root_kind) in enumerate(
        [
            (WEAPON, "WeaponNamePage", "WeaponsPage"),
            (COUNTRY, "CountriesPage", "ArmedForcesListPage"),
            (COMPANY, "FIReportCompanyPage", "DefenseCompanyListPage"),
            (GROUP, "MilitaryOrganizationPage", "MilitaryOrganizationListPage"),
        ]
    ):
        sections.append(
            {
                "id": i + 1,
                "title": path.split("/")[1],
                "url": "/" + path.split("/")[1] + "/",
                "page_type": root_kind,
                "children": [
                    {
                        "id": 100 + i,
                        "title": "Example " + str(i),
                        "url": path,
                        "page_type": kind,
                        "children": [],
                    },
                ],
            }
        )
    return {"sections": sections}


def detail(path: str, *, restricted: bool = False) -> dict:
    index = {WEAPON: 0, COUNTRY: 1, CHAPTER: 1, COMPANY: 2, GROUP: 3}[path]
    chapter = index in {0, 1}
    props = {
        "id": 200 + index if chapter else 100 + index,
        "title": "Force Structures"
        if path == CHAPTER
        else "Overview"
        if chapter
        else "Example " + str(index),
        "restricted": restricted,
        "restriction_type": "login" if restricted else "trial",
        "seo": {"seo_og_url": "https://www.militaryperiscope.com" + path},
        "parent_items": [
            {"title": "Example " + str(index), "url": path.rsplit("/", 2)[0] + "/"}
        ],
        "is_archived": index == 3,
        "major_update_date": "2020-01-01T00:00:00Z",
        "related_url": [{"title": "Force Structures", "url": CHAPTER}]
        if path == COUNTRY
        else [],
    }
    blocks = [
        {
            "type": "description",
            "value": [
                {
                    "type": "content",
                    "value": '<p>A complete example description with <b>uncertain</b> claims, 80 mi (130 km) range, and unmodified source wording.</p><pre>Variant A    10\nVariant B    20</pre><a href="/weapons/other/overview/">Other equipment</a>',
                }
            ],
        },
        {
            "type": "table",
            "value": {
                "data": [["Mass", "4,960 lb (2,250 kg"], ["Range", "unknown"]],
                "table_header_choice": "neither",
            },
        },
        {
            "type": "image",
            "value": {
                "path": "/wt/media/example.jpg",
                "title": "Example photograph",
                "source": "Example credit",
            },
        },
    ]
    props["section" if index == 0 else "content"] = blocks
    return {
        "component_name": "WeaponDetailPage"
        if index == 0
        else "MilitaryOrganizationPage"
        if index == 3
        else "FIReportPage",
        "component_props": props,
    }


def captures() -> dict[str, bytes]:
    data = {TOC: encoded(toc()), TRIAL: b"__NEXT_DATA__ Redistribution"}
    for node in catalog(data[TOC]):
        if node in {WEAPON, COUNTRY, COMPANY, GROUP}:
            data[page_url(node)] = encoded(detail(node))
        else:
            data[page_url(node)] = encoded(
                {
                    "component_name": "WeaponsPage",
                    "component_props": {"id": 1, "title": "Index", "restricted": False},
                }
            )
    data[page_url(CHAPTER)] = encoded(detail(CHAPTER))
    return data


def test_trial_catalog_deduplicates_native_identity_and_merges_categories() -> None:
    data = toc()
    duplicate = deepcopy(data["sections"][0])
    duplicate.update(
        id=30,
        title="Another category",
        url="/weapons/another/",
        page_type="WeaponsCategoryPage",
    )
    data["sections"][0]["children"].append(duplicate)
    result = catalog(encoded(data))
    assert result[WEAPON]["id"] == 100
    assert "Another category" in result[WEAPON]["categories"]
    duplicate["children"][0]["id"] = 999
    with pytest.raises(ValueError, match="Conflicting"):
        catalog(encoded(data))


def test_scope_cookie_loading_and_rejection_do_not_reveal_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = MilitaryPeriscope()
    cookie = tmp_path / "session"
    cookie.write_text("sessionid=fixture-only; csrftoken=fixture", encoding="utf-8")
    monkeypatch.delenv("MILITARYPERISCOPE_COOKIE", raising=False)
    monkeypatch.setenv("MILITARYPERISCOPE_COOKIE_FILE", str(cookie))
    assert source.request_headers(TOC)["Cookie"] == cookie.read_text()
    assert source.request_headers(TOC)["Accept"] == "application/json"
    for url in [
        API + "?html_path=%2Faccounts%2F",
        page_url(WEAPON) + "&html_path=/news/",
        page_url(WEAPON).replace("militaryperiscope.com", "evil.test"),
        ORIGIN + "/_next/data/stale/weapons.json",
        API + "?html_path=%2Fweapons%2F..%2Faccounts%2F",
    ]:
        assert source.normalize(url) is None
        with pytest.raises(ValueError, match="outside"):
            source.request_headers(url)
    monkeypatch.setenv("MILITARYPERISCOPE_COOKIE", "sessionid=private\r\nInjected: bad")
    with pytest.raises(ValueError) as failure:
        source.request_headers(TOC)
    assert "private" not in str(failure.value)


def test_cookie_is_not_sent_to_robots_or_outside_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MILITARYPERISCOPE_COOKIE", "sessionid=fixture-only")
    crawler = cast(
        Crawler, SimpleNamespace(spider=SimpleNamespace(source=MilitaryPeriscope()))
    )
    middleware = ScopeMiddleware(crawler)
    request = Request(TOC)
    middleware.process_request(request)
    assert request.headers["Cookie"] == b"sessionid=fixture-only"
    robots = Request(ORIGIN + "/robots.txt")
    middleware.process_request(robots)
    assert "Cookie" not in robots.headers
    outside = Request("https://evil.test/")
    with pytest.raises(IgnoreRequest):
        middleware.process_request(outside)
    assert "Cookie" not in outside.headers


def test_discovery_only_follows_trial_tree_and_subject_sections() -> None:
    source = MilitaryPeriscope()
    assert page_url(COMPANY) in source.discover(TOC, encoded(toc()))
    assert source.discover(page_url(COUNTRY), encoded(detail(COUNTRY))) == [
        page_url(CHAPTER)
    ]
    assert source.discover(page_url(WEAPON), encoded(detail(WEAPON))) == []
    data = detail(COUNTRY)
    data["component_props"]["related_url"].append(
        {"url": "/armedforces/africa/other/overview/"}
    )
    with pytest.raises(ValueError, match="related section"):
        source.discover(page_url(COUNTRY), encoded(data))


def test_missing_sections_expired_access_and_subscription_only_chapters() -> None:
    source = MilitaryPeriscope()
    data = captures()
    del data[page_url(CHAPTER)]
    with pytest.raises(ValueError, match="Incomplete"):
        source.prepare(data.items())
    data[page_url(CHAPTER)] = encoded(detail(CHAPTER, restricted=True))
    source.prepare(data.items())
    assert source.restricted == {page_url(CHAPTER)}
    assert source.extract(page_url(CHAPTER), data[page_url(CHAPTER)], []) == []
    expired = detail(COUNTRY, restricted=True)
    expired["component_props"]["restriction_type"] = "trial"
    with pytest.raises(PermissionError):
        source.discover(page_url(COUNTRY), encoded(expired))
    data[page_url(COUNTRY)] = encoded(detail(COUNTRY, restricted=True))
    with pytest.raises(PermissionError):
        source.prepare(data.items())


def test_response_identity_tables_credits_and_historical_dates_are_preserved() -> None:
    source = MilitaryPeriscope()
    data = captures()
    source.prepare(data.items())
    entity = source.extract(
        page_url(WEAPON), data[page_url(WEAPON)], ["Unrelated component"]
    )[0]
    assert entity.key == "100" and entity.aliases == []
    evidence = entity.evidence[0]
    for value in [
        "4,960 lb (2,250 kg",
        "80 mi (130 km)",
        "unknown",
        "Variant A    10",
        "Example credit",
    ]:
        assert value in evidence.markdown
    assert ORIGIN + "/wt/media/example.jpg" in evidence.links
    assert "example.jpg" not in evidence.search_text
    assert "2020-01-01" in evidence.markdown
    renamed = detail(WEAPON)
    renamed["component_props"]["seo"]["seo_og_url"] = ORIGIN + COMPANY
    with pytest.raises(ValueError, match="requested page"):
        source.extract(page_url(WEAPON), encoded(renamed), [])


@pytest.mark.parametrize(
    ("categories", "key", "expected"),
    [
        (["Weapons", "Artillery", "Aircraft Guns"], 1, EntityKind.WEAPON),
        (["Naval Warfare", "Torpedoes"], 2, EntityKind.WEAPON),
        (["Naval Warfare", "Naval Radars"], 406350, EntityKind.SENSOR),
        (["Aircraft", "Aerostats"], 3, EntityKind.AIRCRAFT),
        (["Electronics", "Ground Radars"], 4, EntityKind.RADAR),
        (["Unmanned", "Unmanned Ground Vehicles"], 5, EntityKind.VEHICLE),
    ],
)
def test_kind_does_not_confuse_platforms_and_components(
    categories: list[str], key: int, expected: EntityKind
) -> None:
    assert (
        entity_kind(
            {"id": key, "page_type": "WeaponNamePage", "categories": categories}
        )
        == expected
    )


def test_unknown_content_blocks_fail_instead_of_silently_truncating() -> None:
    with pytest.raises(ValueError, match="Unsupported"):
        render([{"type": "new-layout", "value": {"text": "Important detail"}}])


def test_offline_publication_merges_country_sections_and_exports_exact_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction attempted network access")

    monkeypatch.setattr(socket, "socket", no_network)
    source = MilitaryPeriscope()
    source.minimum_entities = 4
    directory = tmp_path / source.id
    archive = Archive(directory / "archives" / "fixture")
    data = captures()
    for url, body in data.items():
        archive.add(url)
        archive.save(
            url, 200, body, "application/json" if url != TRIAL else "text/html", {}
        )
    archive.mark_complete(source.id)
    try:
        snapshot = publish(source, archive, directory)
        entities = [
            json.loads(line)
            for line in (snapshot / "entities.jsonl").read_text().splitlines()
        ]
        assert len(entities) == 4
        country = next(e for e in entities if e["source_id"] == "101")
        assert country["url"] == ORIGIN + COUNTRY
        assert len(country["evidence"]) == 2
        for e in entities:
            for page in e["evidence"]:
                assert (
                    base64.b64decode(page["source_response"]["body_base64"])
                    == data[page["url"]]
                )
        collect_artifacts(tmp_path, [source.id])
    finally:
        archive.close()


def test_expired_crawl_stops_and_never_archives_cookie_headers(tmp_path: Path) -> None:
    archive = Archive(tmp_path / "archive")
    spider = ArchiveSpider(MilitaryPeriscope(), archive)
    request = spider.request(page_url(WEAPON))
    assert request is not None
    data = detail(WEAPON, restricted=True)
    data["component_props"]["restriction_type"] = "trial"
    response = Response(
        request.url,
        request=request,
        body=encoded(data),
        headers={"Content-Type": "application/json", "Set-Cookie": "sessionid=private"},
    )
    try:
        with pytest.raises(CloseSpider):
            list(spider.capture(response))
        assert archive.counts() == {"failed": 1}
        assert "private" not in archive.pages("failed")[0]["headers"]
        assert not (archive.path / "complete.json").exists()
    finally:
        archive.close()
