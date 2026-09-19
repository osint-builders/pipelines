# pipelines

Standalone website scrapers that archive original HTML, convert it to Markdown, and
publish a fast SQLite FTS5 search index. Each website supplies an adapter. The shared
pipeline handles crawling, resume, validation, publication, and offline search.

The first adapter covers Radartutorial's English radar records and tutorial pages.
The completed dataset contains 2,350 searchable documents. See the
[verification report](docs/VERIFICATION.md) for the corpus and measured search results.

## Quick start

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then:

```sh
git clone https://github.com/osint-builders/pipelines.git
cd pipelines
uv sync --frozen --extra build
uv run --no-sync pipelines --help
```

Python 3.13 is required. uv can install it from the repository's `.python-version`.
Run the first full scrape explicitly, using a storage directory outside this checkout:

```sh
uv run --no-sync pipelines --root ../pipeline-data build radartutorial
```

Only `build` contacts the source website. Repeating it resumes unfinished work or starts
a fresh full scrape after a successful run. Nothing schedules or triggers a scrape
automatically. Status, search, document reads, and reindexing use local files.

`--root` is required on every command. It names the directory containing source folders,
such as `radartutorial/`. Use the same root when building and reading a dataset.

## Use an existing dataset

Existing archives and indexes remain compatible. Point at their current storage root;
there is no migration or new scrape:

```sh
uv run --no-sync pipelines --root "$HOME/.hai/reference-data" status radartutorial
uv run --no-sync pipelines --root "$HOME/.hai/reference-data" search radartutorial "ASR-12"
```

On Windows, that existing root is typically `C:\Users\<you>\.hai\reference-data`.
The repository contains code and synthetic test fixtures. Generated HTML, Markdown,
metadata, and databases stay in the selected data directory.

## Search and read

```sh
uv run --no-sync pipelines --root ../pipeline-data search radartutorial "ASR-12"
uv run --no-sync pipelines --root ../pipeline-data search radartutorial "" --kind radar --category 10.weather
uv run --no-sync pipelines --root ../pipeline-data read radartutorial DOCUMENT_ID
uv run --no-sync pipelines --root ../pipeline-data status radartutorial
```

Commands return JSON. Search results include document IDs, titles, source URLs, kinds,
and snippets. `read` returns metadata, technical facts, and Markdown for one document.

The Python reader needs only the standard library. Install this project with `pip install .`
in a consuming environment, without the optional `build` extra:

```python
from pathlib import Path
from pipelines import Dataset

with Dataset(Path("/reference-data/radartutorial/published")) as dataset:
    results = dataset.search("ASR-12", kind="radar", limit=20)
    document = dataset.read(results[0]["id"])
    filters = dataset.facets()
```

Mount datasets read-only in consuming services. Readers never fetch missing content or
rebuild an index. See [the architecture](docs/ARCHITECTURE.md) for search behavior and
the source adapter interface.

## Docker

Build the image from this repository:

```sh
docker build --tag osint-pipelines:local .
```

Replace `/absolute/path/to/pipeline-data` with the host's data directory:

```sh
docker run --rm --init --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local --root /data build radartutorial
docker run --rm --network none --volume /absolute/path/to/pipeline-data:/data:ro osint-pipelines:local --root /data search radartutorial "ASR-12"
```

On Linux, add `--user "$(id -u):$(id -g)"` when the container should write as your user.
The host directory must already be writable by that user.

Rebuild Markdown and the index from saved HTML with networking disabled. Use the archive
ID from `published/current.json`:

```sh
docker run --rm --network none --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local --root /data reindex radartutorial ARCHIVE_ID
```

## Development

```sh
uv sync --frozen --extra build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests
uv run --no-sync pytest
uv build
```

Tests use synthetic HTML and a local fixture server. They do not crawl Radartutorial.
GitHub Actions runs the suite on Linux and Windows and checks the Docker image.

```text
src/pipelines/          Shared archive, crawler, builder, and reader
src/pipelines/sources/ Website adapters
src/pipelines/sources.toml
tests/                 Extraction, resume, publication, CLI, and reader tests
docs/                  Architecture and verification
```

## Source content

Radartutorial documents retain source links and attribution. Images, equations, PDFs,
and other attachments remain links; the pipeline does not download or OCR those assets.
Consult the [source's copyright page](https://www.radartutorial.eu/html/copyright.en.html)
and individual image credits for content terms.
