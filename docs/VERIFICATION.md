# Radartutorial verification

Verified on 2026-09-19 UTC. The initial English archive and its searchable snapshot are
complete. Scraping remains an explicit operation through `pipelines build`.

## Corpus

| Item | Result |
| --- | ---: |
| Successfully archived responses | 2,405 |
| Searchable documents | 2,350 |
| Radar records | 1,736 |
| Other English pages | 614 |
| Archived discovery/noindex responses excluded from search | 55 |
| Source URLs returning HTTP 404 | 11 |
| Redirects excluded from the English source scope | 3 |
| Pending or failed requests after completion | 0 |

The excluded redirects comprise two external license sites and one German-language
page. The 404 response bodies remain in the archive alongside successful HTML and the
English XML sitemap. There are 2,416 saved response files in total.

Archive: `20260919T012658Z-4b3ddafd`.
Final snapshot: `20260919T021052Z-4e8aa5cf`.
Index size: 36,298,752 bytes.

All successful response hashes were checked during the offline build. The archive and
search database passed SQLite integrity checks. Data lives under
`~/.hai/reference-data/radartutorial`, outside the checkout.

## Functional checks

- Exact lookups returned the expected ASR 12, P-18, TerraSAR-X, and KALKAN records.
- Weather-radar filtering returned 76 records.
- Technical values, operating-mode qualifiers, reference links, image credits, and
  scientific subscripts/superscripts survived conversion.
- Full-corpus inspection found and covered malformed navigation comments, unclosed
  mobile headings, script-based navigation, and an image-only help page.
- A complete HTML-to-Markdown-to-index rebuild succeeded in Docker with
  `--network none`.
- Search and document reads succeeded with a read-only mount and Python `-S`.
  Scrapy, Beautiful Soup, Markdownify, and FileLock were not loaded by the reader.
- Two containers contending for a lock on the actual Windows bind mount correctly
  allowed only one writer.
- Explicit builds, interrupted discovery, HTTP failure and resume, offline extraction,
  corrupt archives, failed publication, old-reader stability, and missing datasets
  are covered by tests.

The first live crawl encountered an archive I/O error during cross-platform inspection
of its live SQLite file. Publication was blocked. The explicit resume reused saved
responses, completed the remaining URLs, and published successfully. Status now reads
JSON progress files and immutable snapshots instead of the live archive database.

## Lookup performance

Measured against the final snapshot in Docker Desktop on Windows, using a read-only
bind mount with networking disabled. Runtime: Python 3.13.11, SQLite 3.40.1.

| Measurement | Milliseconds |
| --- | ---: |
| Median | 2.387 |
| 95th percentile | 27.098 |
| Maximum | 27.978 |

This is 240 warm searches: 12 queries, repeated 20 times, returning up to 20 results.
Queries included exact equipment names, radar theory, and broad terms such as
`radar`, `frequency`, and `weather`. Timings measure the reader call; they exclude
connection startup and any future application transport.

The reader calculates snippets only for selected results, carries the best matching
section into document lookup, and uses a bounded page cache. Carrying the selected
section avoids repeated scans of a window-query result on the container's older SQLite.
The measured 95th percentile meets the proposed 50 ms warm-query target.

## Initial implementation checks

The pipeline suite passed 25 tests on Windows and Linux. Lint, formatting, and type
checks passed with the versions in its lockfile. These measurements describe the
initial corpus build before extraction into this standalone repository.

Images and PDFs remain linked source assets. This snapshot provides the standalone
reader and CLI; application API routes and agent-tool registration are separate
consumers of that interface.

## Standalone project validation

The standalone `osint-pipelines` distribution passed 28 tests on Windows with Python
3.13.13. Ruff lint/format checks and mypy passed using its own locked environment.
Both the source distribution and wheel built successfully.

The wheel includes the source registry and Radartutorial adapter. Installing only that
wheel in an isolated environment installed one package. Its `pipelines` command queried
the existing ASR 12 record successfully without crawler dependencies.

The Docker image built from this repository queried snapshot
`20260919T035722Z-8e8ccca0` through a read-only mount with networking disabled. The reader
ran with Python `-S` and imported no third-party packages. ASR 12, P-18, and WSR-88D
lookups, Markdown reads, and URL lookup passed. Weather filtering returned 76 records
from the unchanged 2,350-document corpus.
