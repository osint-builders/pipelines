# pipelines

Scrape equipment sources into one offline CLI. Search by name or meaning, then use
a stable source ID to retrieve the complete captured evidence.

## Download the latest CLI

Get the [latest release](https://github.com/osint-builders/pipelines/releases/latest).
Each archive contains one standalone executable with its model, index, and evidence.

| Platform | Download |
| --- | --- |
| Windows x86-64 | [pipelines-windows-amd64.zip](https://github.com/osint-builders/pipelines/releases/latest/download/pipelines-windows-amd64.zip) |
| Linux x86-64 | [pipelines-linux-amd64.tar.xz](https://github.com/osint-builders/pipelines/releases/latest/download/pipelines-linux-amd64.tar.xz) |
| Linux ARM64 | [pipelines-linux-arm64.tar.xz](https://github.com/osint-builders/pipelines/releases/latest/download/pipelines-linux-arm64.tar.xz) |
| macOS Intel | [pipelines-darwin-amd64.tar.xz](https://github.com/osint-builders/pipelines/releases/latest/download/pipelines-darwin-amd64.tar.xz) |
| macOS Apple Silicon | [pipelines-darwin-arm64.tar.xz](https://github.com/osint-builders/pipelines/releases/latest/download/pipelines-darwin-arm64.tar.xz) |

Compare the download's SHA-256 with [SHA256SUMS](https://github.com/osint-builders/pipelines/releases/latest/download/SHA256SUMS)
using `Get-FileHash -Algorithm SHA256` on Windows, `sha256sum` on Linux, or
`shasum -a 256` on macOS. Extract `pipelines` (`pipelines.exe` on Windows) and place
it on your PATH. No Python, API key, model download, or service is needed.

```sh
pipelines verify
pipelines search "russian cheeseboard"
pipelines info
```

Use `./pipelines` or `.\pipelines.exe` to run directly from the extracted directory.
`info` lists the bundled sources and counts; `version` identifies the build.
Download a newer executable to update its fixed dataset.

## CLI API

Pass arguments to the executable and read JSON from stdout. All commands work
offline. Errors return a nonzero exit code and a JSON `error` on stderr.

| Command (prefix with `pipelines`) | Result |
| --- | --- |
| `search [--mode hybrid\|vector] [--limit N] [filters] "query"` | Ranked entities; hybrid adds name/alias matching, vector uses cosine only |
| `similar [--limit N] [filters] SOURCE:ID` | Identity-vector neighbors, excluding the input entity |
| `get [--format json\|markdown\|html\|source] [--evidence PAGE_ID] SOURCE:ID` | Complete entity/evidence; JSON by default |
| `info`, `version` | Dataset/model/counts/sources; executable version |
| `verify`, `--help` | Bundle integrity and embedding parity; command help |

Flags precede the query or ID. Filters are `--source`, `--kind`, and `--category`;
they apply before `--limit` (default 10, range 1–100). Queries support up to
1,000 characters and 256 model tokens. Each search returns at most one result per entity.

```sh
pipelines search --source deagel "M142 HIMARS"
pipelines search --mode vector --kind radar "detect aircraft approaching an airport"
pipelines similar --limit 5 radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format markdown radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format source cambridgepixel:bde71cdc6cc662ed8c60354b > radar-database.html
```

Search JSON contains `dataset_id`, `query`, `mode`, and `results`. Results include
`id`, `title`, `url`, `source`, `kind`, `categories`, `aliases`, `score`, `cosine`,
`name_match`, `snippet`, and `evidence_id`. An empty evidence ID indicates an identity match.

| Export format | Contents |
| --- | --- |
| `json` | Complete entity and all evidence, including Markdown, metadata, and HTML (`html_base64` for non-UTF-8) |
| `markdown` | All retained pages joined, or one selected by `--evidence` |
| `html` | Original HTML or the rendered API/record document; multiple pages require `--evidence` |
| `source` | Exact captured HTML/API response/shared collection; multiple pages require `--evidence` |

API evidence retains `canonical_url` and `source_response` metadata. Shared collection
responses use `source_response.body_member`; inline API responses use `body_base64`.

## Source adapters

| Source ID | Captures |
| --- | --- |
| [radartutorial](https://www.radartutorial.eu/index.en.html) | English radar and equipment pages |
| [deagel](https://www.deagel.com/Armies/) | Equipment families and variants across all four statuses |
| [virtualglobetrotting](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml) | Radar-site feed and linked records |
| [russianforces](https://russianforces.org/atom.xml) | Feed articles with reviewed equipment identities |
| [wikipedia](https://en.wikipedia.org/wiki/Category:Military_radars_of_China) | English military-radar category articles |
| [commons](https://commons.wikimedia.org/wiki/Category:Military_radars_of_Russia) | Equipment categories and file descriptions |
| [armyrecognition](https://www.armyrecognition.com/military-products/army/radars/air-defense-radars) | Air-defense radar product pages |
| [fandom](https://military-history.fandom.com/wiki/Category:Russian_and_Soviet_military_radars) | Military Wiki category membership and articles |
| [climateviewer](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson) | Site records from the Fortress Russia GeoJSON |
| [cambridgepixel](https://cambridgepixel.com/resources/radar-database/) | Radar database ProductModel records |
| [militaryperiscope](docs/sources/militaryperiscope.md) | Trial catalog subjects and related sections |

## Build and refresh

Requires Python 3.13, uv, and Go 1.27. The Python producer archives source responses,
extracts entities and Markdown, and builds a vector bundle. The Go consumer embeds
that bundle and the pinned `all-MiniLM-L6-v2` model for offline search.

```sh
uv sync --frozen --extra build --extra vector
uv run --no-sync pipeline-build sources
uv run --no-sync pipeline-build crawl radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build status radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build extract radartutorial ARCHIVE_ID --root ../pipeline-data
uv run --no-sync pipeline-build model --output build/model
uv run --no-sync pipeline-build package --root ../pipeline-data --source radartutorial --model build/model --cache build/entity-vector-cache --output build/dataset.zip
uv run --no-sync python tools/build_cli.py --bundle build/dataset.zip --output dist/pipelines
```

Repeat `--source SOURCE` when packaging multiple sources. On Windows, use
`dist/pipelines.exe` as the executable output. Only `crawl` requests source websites;
it resumes unfinished archives. `extract` replays saved responses offline.

Under `DATA_ROOT/SOURCE/`, captures live in `archives/CRAWL_ID/`, snapshots in
`published/snapshots/SNAPSHOT_ID/`, and `published/current.json` selects the current
snapshot. Keep generated data, models, caches, and binaries outside Git.

Deagel discovery requires Node.js, `npm install -g agent-browser@0.27.2`, and
`agent-browser install`. The Docker image supports HTTP crawling and offline extraction:

```sh
docker build --tag osint-pipelines:local .
docker run --rm --init --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local crawl radartutorial --root /data
```

## Build and publish binaries

```sh
uv run --no-sync python tools/build_release.py --bundle build/dataset.zip --directory dist/release
uv run --no-sync python tools/release.py publish --repo osint-builders/pipelines --directory dist/release --tag cli-DATASET_ID --target COMMIT_SHA
```

The build produces five standalone executables, compressed platform archives,
`dataset-manifest.json`, and `SHA256SUMS`. It strips debug symbols, uses `CGO_ENABLED=0`,
and compresses downloads with ZIP on Windows and XZ on Linux/macOS. Each archive is
checked against its executable. Cross-compiled targets need runtime checks on their platform.

Use the full dataset ID from the manifest and the committed build's SHA. Publication
requires GitHub CLI authentication and checks all seven assets before publishing the
draft. Existing published assets are never overwritten. Ordinary CI does not publish.

The [manual release workflow](.github/workflows/release-cli.yml) can instead build on
native runners after staging a bundle:

```sh
uv run --no-sync python tools/release.py stage --repo osint-builders/pipelines --bundle build/dataset.zip
gh workflow run release-cli.yml --repo osint-builders/pipelines -f input_tag=data-CONTENT_SHA256
```

## Checks

```sh
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests tools
uv run --no-sync pytest
uv run --no-sync python tools/accept_cli.py dist/pipelines build/dataset.zip
uv run --no-sync python tools/evaluate_cli.py dist/pipelines
cd cli
go test -tags NODOWNLOAD ./...
go vet -tags NODOWNLOAD ./...
```

## Add a source

Implement [Source](src/pipelines/sources/base.py) under `src/pipelines/sources/` and
register it in [sources.toml](src/pipelines/sources.toml). Adapters define scoped URLs,
discovery, stable native IDs, and deterministic extraction into [Entity/Evidence](src/pipelines/model.py).
Preserve complete evidence and original responses; keep aliases specific to the subject.
Add fixtures, offline replay tests, and [CLI retrieval cases](tests/fixtures/retrieval.json).
