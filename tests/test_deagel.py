import hashlib
import socket
from io import BytesIO
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from pipelines.archive import Archive, atomic_json
from pipelines.build import build
from pipelines.distribution import collect, content_digest
from pipelines.sources.deagel import Deagel, kind

BODY = (Path(__file__).parent / "fixtures/deagel.html").read_bytes()
URL = "https://www.deagel.com/Armies/Example%20tank/a123456"
CATALOG = b'<main><h2>Armies</h2><div class="guide"><a href="Armies/Example tank/a123456#001">Example A</a><a href="Armies/Example tank/a123456#002">Example B</a><a href="Components/Engine/a222222#001">Engine</a></div></main>'


def test_scope_identity_and_deduplicated_discovery() -> None:
    source = Deagel()
    assert source.normalize(URL + "?tracking=x#002") == URL
    assert source.normalize("http://deagel.com/Armies/") == source.seeds[0]
    for path in (
        "/News/n123",
        "/Components/Engine/a123456",
        "/Country/Russia",
        "/Armies/../Secret/a123456",
    ):
        assert source.normalize("https://www.deagel.com" + path) is None
    assert source.normalize("https://unrelated.example/Armies/Tank/a123456") is None
    assert source.discover(source.seeds[0], CATALOG) == [URL]
    assert source.discover(URL, BODY) == []
    assert source.extract(source.seeds[0], CATALOG, []) == []


def test_streamed_and_rendered_pages_produce_identical_variant_entities() -> None:
    source = Deagel()
    variants = source.extract(URL, BODY, ["Never borrow another variant's alias"])
    parsed = BeautifulSoup(BODY, "html.parser")
    template = parsed.select_one('template[blazor-component-id="18"]')
    assert template is not None
    rendered = ("<main>" + template.decode_contents() + "</main>").encode()
    assert source.extract(URL, rendered, []) == variants
    assert [entity.key for entity in variants] == ["a123456-001", "a123456-002"]
    assert variants[0].aliases == ["Alpha", "Example A", "Model A"]
    assert variants[1].aliases == ["Example B"]
    first = variants[0]
    first.validate()
    assert first.metadata(source.id)["url"] == URL + "#001"
    assert first.facts[0].evidence == URL + "#001"
    facts = {fact.name: fact.raw for fact in first.facts}
    assert facts["Specifications: Combat Weight"] == "50,000 kilogram | With armor"
    assert facts["Specifications: Production"].startswith("0")
    assert "10/20" in facts["Operators: Exampleland"]
    assert "copper turret" in first.evidence[0].search_text
    assert "titanium chassis" not in first.evidence[0].search_text
    assert "Unrelated news" not in first.evidence[0].search_text
    assert "titanium chassis" in first.evidence[0].markdown
    assert first.evidence[0].markdown == variants[1].evidence[0].markdown
    assert "https://www.deagel.com/library/sample.jpg" in first.evidence[0].markdown
    assert "Loading..." not in first.evidence[0].markdown
    assert "Global navigation" not in first.evidence[0].markdown
    moved = source.extract(URL.replace("Example%20tank", "Renamed"), BODY, [])
    assert moved[0].key == first.key


@pytest.mark.parametrize(
    "body",
    [
        b"<main>Loading...</main>",
        BODY.replace(b"Group : Main Battle Tanks", b"No category"),
    ],
)
def test_incomplete_responses_fail_closed(body: bytes) -> None:
    with pytest.raises(ValueError):
        Deagel().extract(URL, body, [])


def test_passive_sensor_is_not_misclassified_as_a_radar() -> None:
    assert (
        kind(
            "Surveillance Vehicles",
            "The example is a passive acoustic sensor system. It avoids radar emissions.",
        )
        == "sensor"
    )
    assert (
        kind("Surveillance Vehicles", "The example is a surveillance radar.") == "radar"
    )
    assert kind("Main Battle Tanks", "Tracked armored platform.") == "vehicle"
    assert kind("Rocket Artillery Systems", "A rocket launcher.") == "weapon"


def test_offline_extraction_keeps_full_evidence_and_scoped_search(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = Deagel()
    source.minimum_entities = 2
    directory = tmp_path / source.id
    archive = Archive(directory / "archives" / "fixture")
    archive.add(URL)
    archive.save(URL, 200, BODY, "text/html", {})
    archive.mark_complete(source.id)
    archive.close()

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Offline extraction requested browser or network")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(source, "discovery_seeds", forbidden)
    build(source, tmp_path, archive_id="fixture")
    entities, bodies = collect(tmp_path, [source.id])
    assert len(entities) == 2 and len(bodies) == 1
    assert next(iter(bodies.values())) == BODY
    assert "copper turret" in entities[0]["evidence"][0]["search_text"]
    assert "titanium chassis" in entities[0]["evidence"][0]["markdown"]
    digest = content_digest(entities)
    entities[0]["evidence"][0]["html_sha256"] = "different transport bytes"
    assert content_digest(entities) == digest
    entities[0]["evidence"][0]["search_text"] += " New specification."
    assert content_digest(entities) != digest


def test_cached_discovery_is_reused_and_corruption_is_rejected(tmp_path: Path) -> None:
    directory = tmp_path / "discovery"
    directory.mkdir()
    (directory / "armies.html").write_bytes(CATALOG)
    atomic_json(
        directory / "manifest.json", {"sha256": hashlib.sha256(CATALOG).hexdigest()}
    )
    assert Deagel().discovery_seeds(tmp_path) == [URL]
    (directory / "armies.html").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        Deagel().discovery_seeds(tmp_path)


def test_browser_discovery_respects_robots_before_starting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "pipelines.sources.deagel.urlopen",
        lambda *args, **kwargs: BytesIO(b"User-agent: *\nDisallow: /Armies\n"),
    )

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("Browser was started despite robots exclusion")

    monkeypatch.setattr("pipelines.sources.deagel.shutil.which", forbidden)
    with pytest.raises(RuntimeError, match="robots.txt disallows"):
        Deagel().discovery_seeds(tmp_path)
