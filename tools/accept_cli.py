"""Exercise the built executable against its bundled data on a native runner."""

import base64
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path


def accept(binary: Path, bundle: Path) -> None:
    def run(*args: str) -> bytes:
        return subprocess.run(
            [str(binary.resolve()), *args], capture_output=True, check=True, timeout=120
        ).stdout

    info = json.loads(run("info"))
    with zipfile.ZipFile(bundle) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        assert info["dataset_id"] == manifest["dataset_id"]
        search = json.loads(
            run(
                "search",
                "--mode",
                "vector",
                "--limit",
                "3",
                "detection of aircraft approaching an airport",
            )
        )
        assert search["mode"] == "vector" and search["results"]
        assert not any(result["name_match"] for result in search["results"])
        identifier = search["results"][0]["id"]
        index = json.loads(archive.read("index.json"))
        example = "radartutorial:8bdc6ce92fea3ca62de71395"
        if any(document["id"] == example for document in index):
            search = json.loads(
                run(
                    "search",
                    "--source",
                    "radartutorial",
                    "--limit",
                    "3",
                    "russian cheeseboard",
                )
            )
            assert search["results"][0]["id"] == example, search["results"]
            identifier = example
        assert info["entities"] == len(index)
        assert all(item["kind"] != "article" for item in index)
        samples = {item["source"]: item["id"] for item in index}
        samples[identifier.split(":", 1)[0]] = identifier
        for identifier in samples.values():
            document = json.loads(run("get", identifier))
            original = json.loads(
                archive.read("entities/" + identifier.replace(":", "/") + ".json")
            )
            assert all(
                document[key] == value
                for key, value in original.items()
                if key != "evidence"
            )
            assert len(document["evidence"]) == len(original["evidence"])
            for page, exported in zip(
                original["evidence"], document["evidence"], strict=True
            ):
                assert all(exported[key] == value for key, value in page.items())
                assert (
                    run(
                        "get",
                        "--format",
                        "markdown",
                        "--evidence",
                        page["id"],
                        identifier,
                    ).decode()
                    == page["markdown"]
                )
                html = run(
                    "get", "--format", "html", "--evidence", page["id"], identifier
                )
                assert html == archive.read(
                    "html/" + original["source"] + "/" + page["id"] + ".html"
                )
                assert hashlib.sha256(html).hexdigest() == page["html_sha256"]
                captured = run(
                    "get", "--format", "source", "--evidence", page["id"], identifier
                )
                response = page.get("source_response")
                if response:
                    expected = (
                        archive.read(response["body_member"])
                        if "body_member" in response
                        else base64.b64decode(response["body_base64"])
                    )
                    assert captured == expected
                    assert hashlib.sha256(captured).hexdigest() == response["sha256"]
                else:
                    assert captured == html
        neighbors = json.loads(run("similar", "--limit", "3", identifier))
        assert all(item["id"] != identifier for item in neighbors["results"])
        assert len({item["id"] for item in neighbors["results"]}) == len(
            neighbors["results"]
        )
        bad = subprocess.run(
            [str(binary.resolve()), "get", "missing:id"], capture_output=True
        )
        assert bad.returncode != 0 and json.loads(bad.stderr)["error"]
    print(
        json.dumps(
            {
                "accepted": str(binary),
                "dataset_id": info["dataset_id"],
                "entities_checked": samples,
            }
        )
    )


if __name__ == "__main__":
    accept(Path(sys.argv[1]), Path(sys.argv[2]))
