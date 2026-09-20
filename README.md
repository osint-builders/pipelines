# pipelines

Scrape equipment sources into one offline CLI. Search radars, emitters, vehicles, sites,
and other items by name or meaning; follow a stable ID to the complete captured evidence.

**Local dataset:** 10 sources, **4,194 entities**, **4,926 evidence pages**, **15,272 vectors**.
Captured/evaluated September 20, 2026 UTC. Dataset ID:
`00b3d0cad1a8c2b508af65b2e2fdb879dc08eefefb0339a7fecb8fff8cb00998`.

## Architecture

`Explicit crawl -> original responses -> entities + Markdown -> vector bundle -> standalone CLI`

| Component | Contract |
| --- | --- |
| Python producer | Separate from applications; only explicit `crawl` requests source websites. Resumes unfinished archives; successful refresh starts another archive. No startup/scheduled scraping. |
| Adapters | Bounded discovery; stable `source:item-key` identities; aliases, kinds, categories, evidence-backed facts. Multiple entities/pages may share evidence. No automatic cross-source merging. |
| Evidence | Full extracted Markdown, original bytes, URL/title/language, links, attribution, retrieval time, checksums. Preserve units, uncertainty, contradictions, variants. Media/PDFs remain links. |
| Embeddings | Pinned `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, attention-mask mean pooling, normalized vectors; identity plus overlapping evidence chunks. Focused search text never replaces evidence. |
| Go consumer | Embedded model/data/HTML/JSON/Markdown/vectors; exact cosine scan, one result/entity. No API key, Python, download, service, or writable cache. |
| Publication | Source lock; completeness/hash/entity/minimum-size/shrinkage checks; atomic snapshot pointer. Failures preserve previous data; archives/history retained. |

## Build and refresh

Requires **Python 3.13, uv, Go 1.27**. Store producer data outside application/repository directories.

```sh
uv sync --frozen --extra build --extra vector
uv run --no-sync pipeline-build sources
uv run --no-sync pipeline-build crawl radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build status radartutorial --root ../pipeline-data
# Re-extract saved bytes without another crawl:
uv run --no-sync pipeline-build extract radartutorial ARCHIVE_ID --root ../pipeline-data
uv run --no-sync pipeline-build model --output build/model
uv run --no-sync pipeline-build package --root ../pipeline-data --source radartutorial --model build/model --cache build/entity-vector-cache --output build/dataset.zip
uv run --no-sync python tools/build_cli.py --bundle build/dataset.zip --output dist/pipelines
uv run --no-sync python tools/accept_cli.py dist/pipelines build/dataset.zip
uv run --no-sync python tools/evaluate_cli.py dist/pipelines --output build/retrieval-evaluation.json
```

- Crawl each desired source, then repeat `--source SOURCE` when packaging its published snapshot.
- `status` reports archive IDs; re-extraction also migrates older page-based snapshots.
- `model` downloads pinned/checksummed assets; packaging subsequently works offline,
  caching embeddings by model/text. Windows: use `dist/pipelines.exe` for build/accept/evaluate.
- Deagel discovery needs Node.js, `npm install -g agent-browser@0.27.2`, then
  `agent-browser install`; detail extraction needs no browser.

```sh
docker build --tag osint-pipelines:local .
docker run --rm --init --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local crawl radartutorial --root /data
docker run --rm --network none --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local extract radartutorial ARCHIVE_ID --root /data
```

The image lacks Chromium/agent-browser; run initial Deagel discovery on a prepared host.

| Under `DATA_ROOT/SOURCE/` | Contents |
| --- | --- |
| `archives/CRAWL_ID/` | Captures in `html/` (`.json` for APIs), SQLite crawl manifest |
| `published/snapshots/SNAPSHOT_ID/` | `entities.jsonl`, Markdown, metadata, derived HTML where applicable |
| `published/current.json` | Current snapshot pointer |

SQLite is producer bookkeeping; consumer lookup uses embedded catalog/vectors.
Generated archives, models, bundles, and binaries stay outside Git history.
Current workstation data root: `C:/Users/erikz/.hai/reference-data/`; model/cache/bundle:
`build/model/`, `build/entity-vector-cache/`, `build/dataset.zip`; binaries/checksums: `dist/`.
Latest retrieval report: `build/cambridgepixel-evaluation/cli-retrieval.json`;
CMANO access captures: `build/cmano-evaluation/`.

## Release mechanics

1. Crawl/extract, package, build, run acceptance/retrieval checks.
2. Resolve source redistribution requirements; scraping success does not establish rights.
3. Stage verified data; run the exact dispatch command printed by the helper:

   ```sh
   uv run --no-sync python tools/release.py stage --repo osint-builders/pipelines --bundle build/dataset.zip
   gh workflow run release-cli.yml --repo osint-builders/pipelines -f input_tag=data-CONTENT_SHA256
   ```

| Rule | Behavior |
| --- | --- |
| Trigger | [Release CLI](.github/workflows/release-cli.yml) is manual-only; ordinary CI never publishes datasets/binaries. |
| Change gate | Fingerprint covers entity metadata, full Markdown, search text; excludes retrieval timestamps, raw HTML hashes, API envelopes. Transport-only/code-only/model-only changes do not trigger release; model/recipe hashes remain tracked. |
| Unchanged data | Skip build/publication; preserve dataset ID. Byte checksums still protect integrity. |
| Platforms | Native Linux/macOS amd64+arm64 and Windows amd64, `CGO_ENABLED=0`; identical verified bundle. Linux amd64 additionally verifies offline/read-only. |
| Publication | All five jobs and asset-completeness checks pass before publishing draft; existing binaries never overwritten. |
| Artifacts | Input `data-CONTENT_SHA256`; release `cli-DATASET_ID`; five executables, `dataset-manifest.json`, `SHA256SUMS`; embedded notices. |

### Remaining work

- Review redistribution rights/select a permitted source subset; stage and publish the
  first CLI release. Complete native acceptance for both macOS targets and all release jobs.
- Back up producer archives, snapshots, model, and bundle independently of Git.
- Resume CMANO when original pages/export are accessible; current implementation is blocked.
- Improve the six semantic misses below; broaden independent/ambiguous-name evaluations.
  Historical feeds, further categories, and Cold War data require explicit scope review.

## Sources

Counts describe captured scope, not whole-site coverage/current capability. Shared crawling
enforces robots, delays/throttling, and maximum concurrency two.

| Source ID and seed | Corpus | Capture and identity |
| --- | ---: | --- |
| [radartutorial](https://www.radartutorial.eu/index.en.html) | 1,735: 1,710 radars, 25 equipment | English sitemaps/indexes/manufacturers; equipment HTML with tables/tooltips/captions. Tutorials/navigation/events excluded from results. |
| [deagel](https://www.deagel.com/Armies/) | 1,285: 813 vehicles, 394 weapons, 37 radars, 13 sensors, 28 equipment | Browser selects four statuses; HTTP parses late `template[blazor-component-id]`; root-relative links. Native family+variant anchor ID survives slug changes. |
| [virtualglobetrotting](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml) | 100 sites | RSS -> canonical HTML; numeric map IDs; coordinates, locality, contributor, dates, categories, comments. |
| [russianforces](https://feeds.feedburner.com/russianforces/) | 57 entities; 15 articles | Publisher [Atom](https://russianforces.org/atom.xml) matched FeedBurner bytes; full HTML; 34 reviewed equipment identities + 23 literal Cosmos designations. |
| [wikipedia](https://en.wikipedia.org/wiki/Category:Military_radars_of_China) | 41: 39 radars, 2 aircraft | Recursive English category HTML; page IDs, revisions/history, infoboxes, captions, references. |
| [commons](https://commons.wikimedia.org/wiki/Category:Military_radars_of_Russia) | 151: 134 radars, 7 sites, 4 equipment, 3 emitters, 2 vehicles, 1 sensor | Category/file-description HTML; reviewed subject-category IDs; offline graph resolves context/shared evidence. |
| [armyrecognition](https://www.armyrecognition.com/military-products/army/radars/air-defense-radars) | 11: 10 radars, 1 optical sensor | Category 139 HTML; five legacy table/six section layouts. ID: first 24 SHA-256 hex characters of canonical URL; renames require review. |
| [fandom](https://military-history.fandom.com/wiki/Category:Russian_and_Soviet_military_radars) | 46: 32 radars, 14 sites; 53 articles | Public MediaWiki membership/parse APIs; reviewed duplicate grouping; preferred article ID. 55 responses including membership/rights. |
| [climateviewer](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson) | 383: 291 radar, 65 SAM, 22 air bases, 5 ABM sites | Static GeoJSON + [attribution](https://climateviewer.org/history-and-science/government/maps/fortress-russia-air-defence-radar-sam-sites/); name+coordinate hash IDs; complete Point records. |
| [cambridgepixel](https://cambridgepixel.com/resources/radar-database/) | 385: 384 radars, 1 passive ESM sensor; 99 manufacturers | Visible rows cross-checked with JSON-LD ProductModel; ID hashes Unicode/whitespace/case-normalized manufacturer+model. |

### Extraction boundaries and findings

- **Deagel:** statuses Active/Under Development/Cancelled/Retired; checksum-cached
  discovery: default 1,162 links/742 families ->
  1,269/798. IMCP + PCP (`a002660`) returned 404: 797 families retained; 1,268 indexed +
  17 detail-only variants. Abrams browser/HTTP agreed on seven variants. Embed variant
  sections; export full families with origin/operator/status/specification metadata.
  Other catalogs/news/gallery pagination excluded.
- **VirtualGlobetrotting:** RSS 100 versus category 497; KML 25. Robots excludes search,
  AJAX, nearby/archive/pagination. All feed/detail titles and coordinates matched; 11
  descriptions absent. Embed title/place/categories/description; comments remain evidence.
  Renamed slugs/repeated titles retain numeric identities.
- **RussianForces:** current feed only; `/cgi-bin/` search disallowed. Full articles
  (670-7,431 characters) matched Atom; summaries 209-280. [Reviewed names](src/pipelines/sources/russianforces_entities.json)
  and context distinguish variants/Bryansk submarine versus city; no inferred Cosmos
  ranges/temporary OBJECT identities. New names need review; unknown mentions remain text.
  Nine entities have multiple articles; embed mentioning paragraphs/labeled rows.
  Both feed adapters retain prior members on explicit refresh.
- **Wikipedia:** 2 categories + 43 articles captured; 2 reviewed non-items excluded.
  HTML/API membership and Type 346 REST text agreed; production uses `/wiki/`, excludes
  `/w/`. Type 1475 redirects to J-20: aircraft, without radar alias. Family articles stay
  single entities; aliases from subject lead/infobox, never comparisons. New kinds and
  unsupported pagination fail for review.
- **Commons:** 196 categories/1,374 files crawled; 182/1,224 retained; 164 unassigned pages
  archived only; 44 shared evidence pages. [Catalog](src/pipelines/sources/commons_categories.json):
  151 subjects/31 contexts/14 ambiguous collections; new IDs need review. Specific variants
  precede families; mixed collections do not establish identity. English descriptions/
  captions feed embeddings; 235 files use identity fallback. Full multilingual evidence
  is `mul`; untagged captions may mix languages. File dates are media dates; category
  membership proves neither origin nor operator. `/w/` APIs, JS-only metadata/media excluded.
- **Army Recognition:** `fighter?task=view&id=139` selects radars; removing query selects
  aircraft (12 cards/page, four pages). New pagination fails: robots excludes `?start=`.
  [Reviewed subjects](src/pipelines/sources/armyrecognition_subjects.json): MSP500 optical;
  `96n6` slug identifies displayed 92N6/92N6E. Pair legacy columns before flattening;
  retain contradictory 50N6A chassis/range claims and raw units/unknowns. Families stay
  single entities; title/identity mismatches fail.
- **Fandom:** HTML/robots/render challenged; independent public API worked without auth/
  cookies. Robots unavailable, not confirmed permissive. Continuation limits 500/10
  yielded identical 53 IDs (six pages at 10); cycles/errors/unreviewed changes fail.
  [Catalog](src/pipelines/sources/fandom_pages.json) groups Mech/Myech, Duga/Woodpecker,
  N019/Rubin, Zaslon, Zhuk; airborne Bars/naval Bars-Muff Cob stay separate. Stable file
  links and excluded maintenance categories neutralize transient thumbnail failures.
- **ClimateViewer:** 2,989,317 bytes: 383 Points + 383 unassigned LineStrings. Overlays
  remain exportable, not attributed performance. Historical 2010 study/2018 path proves
  neither observation date nor current deployment. Validate geometry/bounds/identity;
  retain uncertainty/coordinates/properties/credits. Moving/renaming changes ID; feature
  order/whitespace do not. Every feature's content affects the fingerprint, including
  overlays. Equipment mentions do not create entities.
- **Cambridge Pixel:** browser/HTTP agreed on 385 rows; Legacy/Apply did not filter reliably.
  Initial table: 326 Current/59 Legacy, 334 URLs/51 absent; no owned detail pages. Validate
  seven fields/counts/positions/identities/schema. Preserve combined names; separate
  manufacturers' Watchman records. VERA-NG is passive ESM, Twinvis radar. Renaming requires
  identity review. Full ProductModels retained; 1,289,631-byte HTML stored once.
  Reported update: August 7, 2026; manufacturer names do not establish country.

### Rights and blocked sources

| Source | Required provenance/distribution review |
| --- | --- |
| Radartutorial, Deagel, VirtualGlobetrotting, RussianForces | Preserve credits/original terms, including [Radartutorial copyright](https://www.radartutorial.eu/html/copyright.en.html); retain source qualifications. |
| Wikipedia / Commons | Captured CC BY-SA text license, revisions/history, Markdown transformation; Commons media licenses recorded separately; linked media terms remain distinct. |
| Fandom | Captured [CC-BY-SA](https://www.fandom.com/licensing), version unspecified; imported Wikipedia/GFDL notices and separate media terms retained. |
| ClimateViewer | Jim Lee's [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) noncommercial restriction; Planeman/SOC/Sean O'Connor and other credits. |
| Army Recognition / Cambridge Pixel | Copyright/accuracy qualifications retained; no open redistribution license established. Obtain rights before public dataset/binary distribution. [Army Recognition terms](https://www.armyrecognition.com/legal-information). |

**[CMANO DB](https://cmano-db.com/) is not installed/bundled.** HTTP 403 challenge,
browser verification, in-app 522 origin timeout, robots/detail timeouts; no bypass.
Indexed links duplicated paths (`/sensor/sensor/5474/`) and returned layout-only pages.
Indexed navigation showed database 511, six lists, category/country/type/item selectors,
text search and separate Cold War database; detail/search behavior remains unverified.

Resume with working access/authorized export: verify robots/base URLs/pagination/details;
use category+native IDs (`cmano:sensor-5474`), preserve country/year/configuration variants,
simulation/version/hypothetical labels and full HTML/Markdown. Components are relationships,
not aliases; facilities need not be sites. Review Cold War scope/IDs/rights; test offline
replay, embeddings and CLI exports. Existing tests do not validate CMANO.

## Evaluation and checks

Eight-query pure-cosine diagnostics: **vectors / top-1 hits / top-5 hits** per cell.
Deagel: six families/19 variants; others: full source corpus. Small diagnostics, not general
accuracy estimates. Selected text keeps technical content; full evidence remains exportable.

| Source | Minimal input | Full evidence | Selected input |
| --- | --- | --- | --- |
| Deagel | - | 496 / 5 / 7 | Variant: 92 / 6 / 7 |
| VirtualGlobetrotting | RSS: 200 / 4 / 4 | 305 / 6 / 7 | Identity/place/description: 200 / 6 / 7 |
| RussianForces | - | 367 / 4 / 7 | Entity passages: 139 / 5 / 7 |
| Wikipedia | Lead: 93 / 4 / 6 | 398 / 3 / 7 | Body/specs/captions: 237 / 4 / 7 |
| Commons | Categories: 333 / 6 / 6 | 11,994 / 5 / 6 | Categories/captions: 1,680 / 6 / 6 |
| Army Recognition | Snippets: 22 / 6 / 8 | 130 / 8 / 8 | Without image filenames: 90 / 8 / 8 |
| Fandom | Lead: 124 / 8 / 8 | 682 / 8 / 8 | Body/specs/captions: 418 / 8 / 8 |
| ClimateViewer | Names: 766 / 5 / 5 | 1,758 / 8 / 8 | Descriptions: 766 / 7 / 8 |
| Cambridge Pixel | Names: 770 / 2 / 2 | 1,534 / 7 / 8 | Description/band/status/applications: 770 / 8 / 8 |

- [CLI cases](tests/fixtures/retrieval.json): **63/63 required**, **29/35 optional** pass;
  missing sources reported skipped. Global `russian cheeseboard`: 96L6E first,
  hybrid/vector. Type 1478/Repeynik: vector ranks 11/7, hybrid first.
- Tradeoffs: Apple Orchard full/focused rank 3/20; NORAD 68826 rank 7/13; Commons broad
  stealth query rank 45; Deagel sound/infrared outside top 20. Khotilovo second without name.
- Improvements: Santa Teresa RSS rank 72 -> 1; Cyprus full 24 -> 3; Chekhov 5 -> 1;
  Army Recognition Arrow 5 -> 1, Polish helicopter 2 -> 1; Cambridge passive radar 4 -> 1.
- Passed: **153 Python tests**, lint/format/types, package build, Go tests/vet; offline
  replay, unchanged packaging (`changed: false`). Coverage: scope/identity/schema,
  pagination/resume/failures, preservation, filters/ranking, exact exports, release gates.
- Five binaries built. Windows/Linux amd64 verified; Linux offline/read-only; arm64
  emulated. macOS cross-compiled locally; native release acceptance pending.
- Export checks: all 11 Army Recognition, 53 Fandom articles, 383 ClimateViewer,
  385 Cambridge Pixel records, 198 Duga-1 pages. Latest Linux samples all sources plus
  12 Cambridge records; all 16 Cambridge queries pass Windows/Linux.

Optional misses, source-filtered vector mode; target top five, twenty results retrieved:

| Source | Query | Rank |
| --- | --- | ---: |
| Deagel | passive artillery locator using sound and infrared sensors | >20 |
| VirtualGlobetrotting | radar inside a protective dome on Apple Orchard Mountain | 20 |
| RussianForces | satellite with NORAD identifier 68826 | 13 |
| Wikipedia | Type 1478 airborne fire control radar | 11 |
| Commons | radar to detect stealth aircraft cruise missiles and unmanned aerial vehicles | >20 |
| Commons | Repeynik radar | 7 |

```sh
uv sync --frozen --extra build
uv run --no-sync ruff check .
uv run --no-sync ruff format --check .
uv run --no-sync mypy src tests tools
uv run --no-sync pytest
uv build
cd cli
go test -tags NODOWNLOAD ./...
go vet -tags NODOWNLOAD ./...
```

## Add a source

Implement [Source](src/pipelines/sources/base.py) under `src/pipelines/sources/`; register
in [sources.toml](src/pipelines/sources.toml). Shared producer/CLI need no website-specific edits.

| Member | Responsibility |
| --- | --- |
| `id`, `version`, `seeds`, `minimum_entities` | Namespace, discovery compatibility, initial URLs, publication floor |
| `normalize(url)` | Canonical allowed URL or `None`; enforce host/language/path |
| `discover(url, body)`, `labels(url, body)` | Archived-byte discovery; explicit names keyed by canonical URL |
| `extract(url, body, names)` | Deterministic entities/evidence; `[]` for non-items; no network |
| Optional `discovery_seeds(directory)` | SupplementalDiscovery: per-archive rendered/prior-member discovery; never during offline extraction |
| Optional `prepare(pages)` | PreparedSource: offline relationship pass over archived `(url, bytes)` |

- [Entity/Evidence](src/pipelines/model.py) keys: 1-128 ASCII alphanumeric/`._-`, first
  alphanumeric. Prefer native IDs/reviewed narrative identities. Same key/title/kind merges
  evidence; conflicts fail. Facts cite retained URLs/anchors; aliases identify subjects,
  not components/comparisons. Shared pages retain identical full Markdown. Builder adds
  retrieval/hash metadata; `Entity.url` may select an anchor.
- `Evidence.search_text` selects embedding text; empty means full Markdown. Ordinary
  HTML keeps defaults. API evidence sets `rendered_html`, human `canonical_url`, and API
  request `url`; exact article fragment retained.
- Collections set `record_id`, complete `records`, item HTML/Markdown; evidence ID hashes
  URL+record ID. Include excluded features in semantic fingerprints when changes affect
  original exports. Export metadata below preserves complete responses.
- Kinds: `radar`, `emitter`, `sensor`, `vehicle`, `aircraft`, `spacecraft`, `vessel`,
  `weapon`, `site`, `equipment`, `item`. Country/manufacturer require evidence.
- Add scope/identity/content/failure fixtures, offline replay, CLI retrieval/exports.
  [Catalog fixture](tests/test_sources.py) covers shared/multiple evidence. Bump version
  when discovery changes invalidate unfinished crawls.
- Layout: producer `src/pipelines/`; consumer `cli/`; helpers `tools/`; tests/fixtures
  `tests/`; CI/manual releases `.github/workflows/`.

## CLI API and installation

Download from [GitHub Releases](https://github.com/osint-builders/pipelines/releases), or
build locally if unavailable. Verify `SHA256SUMS`; rename to `pipelines` (`pipelines.exe`
on Windows), place on PATH; Linux/macOS: `chmod +x pipelines`.

| Platform | Asset |
| --- | --- |
| Linux x86-64 / ARM64 | `pipelines-linux-amd64` / `pipelines-linux-arm64` |
| macOS Intel / Apple Silicon | `pipelines-darwin-amd64` / `pipelines-darwin-arm64` |
| Windows x86-64 | `pipelines-windows-amd64.exe` |

| Command (prefix with `pipelines`) | Result |
| --- | --- |
| `search [--mode hybrid\|vector] [--limit N] [filters] "query"` | Ranked entities; default hybrid adds name/alias boost; vector uses cosine only |
| `similar [--limit N] [filters] SOURCE:ID` | Identity-vector neighbors, excluding input entity |
| `get [--format json\|markdown\|html\|source] [--evidence PAGE_ID] SOURCE:ID` | Complete entity/evidence; JSON default |
| `info`, `version`, `notices` | Dataset/model/counts/sources; executable version; model/dependency licenses |
| `verify`, `--help` | Bundle integrity, relationships, normalized vectors, Python/Go parity; usage |

Flags precede query/ID. Filters: `--source`, `--kind`, `--category`, applied before limit
(default 10; range 1-100). Queries: at most 1,000 characters/256 model tokens. No country/
numeric-fact/radius filters, cross-source resolution, or attachment OCR. Consumer commands
never crawl or update data.

```sh
pipelines search "russian cheeseboard"
pipelines search --source deagel "M142 HIMARS wheeled rocket artillery launcher"
pipelines search --mode vector --kind radar "detect aircraft approaching an airport"
pipelines similar --limit 5 radartutorial:8bdc6ce92fea3ca62de71395
pipelines get radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format markdown radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format source cambridgepixel:bde71cdc6cc662ed8c60354b > radar-database.html
```

Search JSON: `dataset_id`, query/mode, `results`; results contain `id`, title, source URL,
kind, categories, aliases, `score`, `cosine`, `name_match`, snippet, `evidence_id`.
Empty evidence ID means identity match; scores are similarity, not confidence/verified facts.

| Export | Contents |
| --- | --- |
| JSON | Complete entity/all evidence: Markdown, provenance, HTML (`html_base64` for non-UTF-8) |
| Markdown | All retained pages joined; optional `--evidence PAGE_ID` selects one |
| HTML | Exact ordinary capture or derived API/article/record document; multiple pages require evidence ID |
| Source | Byte-exact HTML/API JSON/whole shared collection; multiple pages require evidence ID |
| API metadata | `html_origin: api-rendered`, `canonical_url`, `source_response` URL/type/SHA-256/`body_base64` |
| Collection metadata | `html_origin: record-rendered`, complete `records`, checksum-verified `source_response.body_member`: shared `responses/SOURCE/URL_HASH.json` or `.html` |

Failures: nonzero exit, JSON `error` on stderr. All consumer operations work offline.
