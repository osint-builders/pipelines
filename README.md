# pipelines

Scrape equipment websites and distribute their data as a single offline search CLI.
Users can find radars, emitters, vehicles, sites, and other items by name or meaning, follow a
stable entity ID, and retrieve the complete scraped evidence behind that item.

The executable embeds the dataset, HTML, original responses, Markdown, vectors, and query embedding
model. It requires no API key, Python installation, model download, or writable cache.
The Python scraper and release tools run separately from consuming applications.

## Scraper project

```mermaid
flowchart LR
    A[Explicit crawl] --> B[Original response archive]
    B --> C[Source adapter: entities and evidence]
    C --> D[Validated Markdown snapshot]
    D --> E[Embedding index and full-content bundle]
    E --> F[Build and verify CLI binaries]
    F --> G[Offline search, similar, get]
```

A source adapter defines which URLs to fetch and which items to extract. Shared code
handles crawl resume, archived response bytes, provenance, validation, embeddings, and
publication. A page can describe several items, and an item can retain several evidence
pages. Entity identities are independent of evidence-page identities.

Entities carry a canonical name, aliases, kind, categories, and facts with evidence URLs.
IDs are `source:item-key`; matching names do not automatically merge records across
sources. Every evidence page retains full extracted Markdown, its URL, title, language,
links, attribution, retrieval time, and HTML checksum. Original response bytes remain
exportable. Images, PDFs, and other attachments remain links rather than downloaded media.

The pinned model is `sentence-transformers/all-MiniLM-L6-v2`: 384 dimensions, attention-mask
mean pooling, and normalized vectors. The producer embeds an entity identity plus
overlapping evidence chunks. Adapters can select item-specific text for embedding when
several variants share a page; the complete evidence remains exportable. The Go CLI
embeds queries with the same model and scans
the vector matrix for exact cosine nearest neighbors, keeping one result per entity.

### Build a dataset

Development requires Python 3.13, uv, and Go 1.27. Keep source archives outside the repository
and application directories. Commands below assume a checkout of this repository.

```sh
uv sync --frozen --extra build --extra vector
uv run --no-sync pipeline-build sources
uv run --no-sync pipeline-build crawl radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build crawl deagel --root ../pipeline-data
uv run --no-sync pipeline-build crawl virtualglobetrotting --root ../pipeline-data
uv run --no-sync pipeline-build crawl russianforces --root ../pipeline-data
uv run --no-sync pipeline-build crawl wikipedia --root ../pipeline-data
uv run --no-sync pipeline-build crawl commons --root ../pipeline-data
uv run --no-sync pipeline-build crawl armyrecognition --root ../pipeline-data
uv run --no-sync pipeline-build crawl fandom --root ../pipeline-data
uv run --no-sync pipeline-build crawl climateviewer --root ../pipeline-data
uv run --no-sync pipeline-build status radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build model --output build/model
uv run --no-sync pipeline-build package --root ../pipeline-data --source radartutorial --source deagel --source virtualglobetrotting --source russianforces --source wikipedia --source commons --source armyrecognition --source fandom --source climateviewer --model build/model --cache build/entity-vector-cache --output build/dataset.zip
uv run --no-sync python tools/build_cli.py --bundle build/dataset.zip --output dist/pipelines
uv run --no-sync python tools/accept_cli.py dist/pipelines build/dataset.zip
uv run --no-sync python tools/evaluate_cli.py dist/pipelines --output build/retrieval-evaluation.json
```

Deagel's full catalog discovery also requires Node.js and a browser on the producer:

```sh
npm install -g agent-browser@0.27.2
agent-browser install
```

On Windows, use `dist/pipelines.exe` in the final three commands. `crawl` is the only command
that requests source websites. It resumes unfinished work, or starts a new full archive
after a successful run. Nothing scrapes on application startup or on a schedule. `model`
downloads pinned, checksum-verified assets; packaging then works offline and caches
embeddings by model and text. Repeat `--source NAME` to package several source snapshots.

To change extraction without crawling again, use the archive ID reported by `status`:

```sh
uv run --no-sync pipeline-build extract radartutorial ARCHIVE_ID --root ../pipeline-data
```

Source storage is `DATA_ROOT/SOURCE/archives/CRAWL_ID/` for original responses and a SQLite crawl
manifest, and `DATA_ROOT/SOURCE/published/snapshots/SNAPSHOT_ID/` for `entities.jsonl`, full
Markdown by evidence ID, and snapshot metadata. `published/current.json` selects the
current snapshot. The archive database is producer bookkeeping; runtime lookup uses the
embedded entity catalog and vectors.

API sources save original JSON as `.json` files in the archive's `html/` response directory.
Their published snapshots additionally contain derived `html/EVIDENCE_ID.html` files.
Each derived HTML page retains the API's exact article fragment inside a minimal document;
the original response is separately preserved in entity metadata and CLI JSON exports.
Structured collections such as GeoJSON instead retain complete item records and a shared
response descriptor. Their original collection is stored once in the bundle and available
through `get --format source`; each item has its own generated HTML and full Markdown.

A per-source lock prevents concurrent writers. Publication checks archive completeness,
HTML hashes, entity validity, minimum corpus size, and unexpected shrinkage before
atomically replacing the current pointer. Failed runs preserve the previous snapshot.
Archives and historical snapshots are retained. Older page-based snapshots can be migrated
by extracting their saved archive again; another crawl is unnecessary.

For containerized crawling of sources other than Deagel, or offline extraction of any source:

```sh
docker build --tag osint-pipelines:local .
docker run --rm --init --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local crawl radartutorial --root /data
docker run --rm --network none --volume /absolute/path/to/pipeline-data:/data osint-pipelines:local extract radartutorial ARCHIVE_ID --root /data
```

The producer image does not include agent-browser or Chromium. Run Deagel's initial
catalog discovery on a host with those dependencies; extraction and packaging need no browser.

### Repository and tests

| Location | Purpose |
| --- | --- |
| `src/pipelines/sources/`, `src/pipelines/sources.toml` | Website adapters and registration |
| `src/pipelines/` | Archive, crawl, entity snapshots, embedding bundle, producer CLI |
| `cli/` | Offline Go search and retrieval executable |
| `tools/` | Bundle verification, binary builds, acceptance checks, releases |
| `tests/`, Go `*_test.go` files | Producer, adapter, consumer, and release tests |
| `.github/workflows/` | Code checks and explicitly dispatched CLI releases |

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

Tests use synthetic source fixtures and a local HTTP server. They cover crawl/resume,
failed-publication preservation, multiple sources, shared and merged evidence pages,
ranking, filters, exact exports, embedding integrity, and release gating. Real binary
acceptance runs against its own bundle. `pipelines verify` checks every bundle member,
entity/evidence relationships, normalized vectors, and Python/Go embedding parity.
The retrieval evaluator runs the source-specific cases in `tests/fixtures/retrieval.json`
against a real executable. Named-item regressions gate releases; broader semantic cases
report ranking quality separately. Missing sources are explicitly reported as skipped.

## Release mechanics

Releases are data-driven and manually initiated. Scraping, packaging, and releasing are
separate operations. The [Release CLI workflow](.github/workflows/release-cli.yml) has
only a manual dispatch trigger; ordinary CI never publishes a dataset or executable.

1. Explicitly crawl or extract the selected sources, then package their snapshots.
2. Build the CLI locally and run the acceptance and retrieval evaluation commands above.
3. Stage the verified bundle as a GitHub data prerelease:

   ```sh
   uv run --no-sync python tools/release.py stage --repo osint-builders/pipelines --bundle build/dataset.zip
   ```

4. Run the exact dispatch command printed by that helper:

   ```sh
   gh workflow run release-cli.yml --repo osint-builders/pipelines -f input_tag=data-CONTENT_SHA256
   ```

The release gate compares the new content fingerprint with the latest CLI release.
Unchanged content skips building and publishing. The fingerprint includes extracted entity
metadata, full Markdown, and selected search text, but excludes retrieval timestamps and
raw HTML hashes and API response envelopes. Repeated Deagel responses contain changing Blazor transport bytes even
when the extracted content is identical. HTML checksums still protect archived bytes and
bundle integrity; transport-only changes do not trigger a release. Model/recipe hashes are
tracked separately; code-only and model-only changes do not trigger a CLI release.

Every platform job consumes the exact same verified bundle. Native runners build and test
Linux/macOS amd64 and arm64, plus Windows amd64, with `CGO_ENABLED=0`. Linux amd64 also
verifies inside a read-only container with networking disabled. All five builds must pass
before publication. A draft becomes public only after its assets are checked for
completeness. Existing published binaries are never overwritten.

Data input tags use `data-CONTENT_SHA256`; CLI release tags use `cli-DATASET_ID`. Releases
contain five executables, `dataset-manifest.json`, and `SHA256SUMS`. The content-addressed
bundle contains the entity catalog, complete evidence, original HTML, embedding vectors,
model assets, and checksums. Generated archives, models, bundles, and binaries stay out
of Git history. License notices remain embedded and available through the CLI.

## Sources

| Source | Searchable content | Current built corpus |
| --- | --- | --- |
| [Radartutorial](https://www.radartutorial.eu/index.en.html) | English equipment catalog pages | 1,735 entities: 1,710 radar and 25 equipment |
| [Deagel Armies](https://www.deagel.com/Armies/) | English land equipment and its variants, across all four status filters | 1,285 entities from 797 family pages |
| [VirtualGlobetrotting Radar Sites](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml) | Geographic records linked from the rolling RSS feed | 100 sites from 100 detail pages |
| [Russian Strategic Nuclear Forces](https://feeds.feedburner.com/russianforces/) | Named equipment and satellites mentioned in the rolling Atom feed | 57 entities with 15 full articles as evidence |
| [Wikipedia: Military radars of China](https://en.wikipedia.org/wiki/Category:Military_radars_of_China) | English category members and their subcategories | 41 entities: 39 radars and two aircraft |
| [Wikimedia Commons: Military radars of Russia](https://commons.wikimedia.org/wiki/Category:Military_radars_of_Russia) | Named equipment categories with category and media-description evidence | 151 equipment and site identities |
| [Army Recognition: Air Defense Radars](https://www.armyrecognition.com/military-products/army/radars/air-defense-radars) | Equipment articles in category 139 | 11 entities: ten radars and one optical sensor |
| [Fandom Military Wiki: Russian and Soviet military radars](https://military-history.fandom.com/wiki/Category:Russian_and_Soviet_military_radars) | Reviewed category articles from the public MediaWiki API | 46 entities: 32 radars and 14 sites, with 53 article pages |
| [ClimateViewer Fortress Russia](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson) | Historical GeoJSON site markers | 383 sites: 291 radar, 65 SAM, 22 air bases, and 5 ABM |

Radartutorial discovery follows English sitemaps, indexes, manufacturer names, and links.
The crawler obeys robots.txt, limits concurrency to two, and applies delay/throttling.
Tutorials, navigation pages, and event reports do not become search results. They may
remain in the discovery archive. Extraction retains technical tables, tooltips, captions,
references, and attribution, while removing duplicate layout. Source content retains its
[original terms](https://www.radartutorial.eu/html/copyright.en.html) and individual credits.

### Deagel discovery and extraction

The Armies catalog offers keyword, status, group, origin, and operator controls. Their
interactive updates do not expose navigable query URLs. The default catalog contains
1,162 variant links across 742 family pages; selecting Active, Under Development,
Cancelled, and Retired reveals 1,269 links across 798 family pages. Discovery uses one
agent-browser session to select all statuses, saves the rendered catalog with a checksum,
and reuses it when resuming the archive. Browser discovery checks robots.txt before
opening the catalog. The shared HTTP crawler fetches the deduplicated family URLs with
robots enforcement, throttling, and resume support.

Detail responses are Blazor streams: the initial HTML says "Loading", but a later
`template[blazor-component-id]` contains the equipment data. Re-parsing that template
produces the completed detail component without rendering every page in a browser.
Relative links resolve against the site's root base URL. A native family ID and variant
anchor produce IDs such as `deagel:a000516-003` for M1A2 Abrams; renamed URL slugs do not
change that identity. The entity URL points directly to its variant anchor.

Extraction retains variant names and aliases, source categories, origin/operator/status
metadata, descriptions, and specification tables with original units and notes. Each
variant embeds only its own section. Full family Markdown, visible news/gallery links,
and original HTML remain available through `get`. Other site catalogs, linked articles,
additional news/gallery pagination, and media downloads are outside this source's scope.
Source assertions and copyright notices are retained as published; they are not independent
verification of equipment specifications.

The September 20, 2026 UTC crawl saved 797 of 798 catalog family pages. The IMCP + PCP
page (`a002660`) returned HTTP 404 and is recorded as unavailable. Family detail pages
also expose 17 variants absent from the filtered index: 1,268 catalog entries plus those
17 variants produce 1,285 entities. These include 813 vehicles, 394 weapons, 37 radars,
13 sensors, and 28 other equipment records. Publication succeeded after validating every
entity and retained page; the same archive was then extracted in a container with
networking disabled.

Evaluation compared raw HTTP, rendered browser content, offline extraction, and retrieval.
The streamed Abrams response and browser rendering exposed the same seven variants.
A six-family, 19-variant embedding comparison used eight queries: selecting variant
sections reduced chunks from 496 to 92 and increased top-one matches from 5/8 to 6/8;
both approaches achieved 7/8 within the first five. This small diagnostic supported
variant-scoped embeddings, but is not a broad quality benchmark. The generic query
"passive artillery locator using sound and infrared sensors" still ranked Penicillin
seventh in that sample; model similarity alone does not reliably infer every capability.
Repeat-response checks also confirmed identical extraction despite different transport
bytes. Automated tests cover both streamed and rendered pages, stable IDs, table fidelity,
scope exclusions, robots rejection, cached discovery, and offline re-extraction.

The initial Deagel/Radartutorial evaluation used 3,020 entities, 10,972 vectors, and 2,532
evidence pages. All six named-item regression queries returned their expected entity first. In the full
Deagel corpus, the range/target-count radar query ranked its expected variant fourth,
while the generic sound-and-infrared query placed Penicillin outside the first 20.
These diagnostic misses are reported explicitly rather than treated as successful
retrieval. Exact HTML/Markdown exports passed for both sources, and packaging the
unchanged snapshots returned `changed: false` with the same dataset ID.

### VirtualGlobetrotting discovery and extraction

The [Radar Sites RSS feed](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml)
is the discovery boundary. It currently exposes the 100 most recent records, including
GeoRSS points and descriptions. The category page reports 497 entries with thumbnail,
list, and map views; sorting by title, latest, views, or rating; and Google/Bing imagery
filters. The linked Earth/KML export contains only the first 25 entries. Neither feed
nor KML is a complete category export. The site's robots.txt excludes search, AJAX,
nearby/archive views, and category pagination; the adapter does not request those URLs.

The scraper archives RSS and follows only its canonical detail links. These pages expose
the description (when supplied), numeric map ID, coordinates, locality/region/country, contributor,
publication/modification dates, categories, references, and comments directly in HTML.
No browser is required by the scraper. An agent-browser comparison of Bullen Point
confirmed the same description, place name, and coordinates as the HTTP response.
The RSS feed is useful for discovery; detail pages provide the additional metadata and
complete record evidence. In this crawl, RSS descriptions matched detail prose exactly;
the benefit of fetching detail pages was their IDs, place names, references, and comments.

IDs use the site's numeric map ID, for example `virtualglobetrotting:311208` for Bullen
Point. A renamed slug does not change that ID. Records use `kind: site`: they describe
geographic observations or facilities, which may contain several systems, historic
equipment, or a source-reported event. They are not automatically merged with equipment
models or other map records at similar coordinates. Latitude/longitude are validated
numeric facts in degrees, and contributor dates preserve the age of the source's claims.
The CLI does not currently provide distance/radius search.

Search embeddings use the record title, place, categories, and description. Complete
record Markdown also retains dates, coordinates, references, map links, and attributed
comments. Raw HTML remains byte-for-byte exportable. Navigation, neighboring sites,
view counters, ratings, and forms are excluded from Markdown and embeddings. Comments
remain in the evidence but do not influence similarity ranking. Imagery and external
references remain links; the crawler does not fetch them. Source descriptions are
community contributions, retained with their original attribution and rights.

Each explicit refresh fetches the current RSS members and previously published detail
URLs. The latter are saved with a checksum in the new archive, so resuming a crawl uses
the same membership and older records are not lost merely because they leave the rolling
feed. Discovery does not run during offline extraction or consumer CLI use. The initial
dataset covers the feed's 100 records, not all 497 category entries.

The September 20, 2026 UTC crawl saved all 100 detail pages and the RSS response with
HTTP 200. All 100 titles and coordinate pairs agreed between RSS and HTML. Eleven
records legitimately contain no description; their available metadata is retained without
inventing prose. Repeated titles remain separate records under their numeric IDs. The
saved archive was re-extracted in a container with networking disabled, and unchanged
repackaging retained the same dataset ID.

Eight source-filtered queries compared three representations using the pinned model and
pure vector ranking. These are small diagnostic evaluations, not a general accuracy claim:

| Embedding input | Vectors for 100 sites | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| RSS text and coordinates | 200 | 4/8 | 4/8 |
| Full extracted record | 305 | 6/8 | 7/8 |
| Title, place, categories, and description (selected) | 200 | 6/8 | 7/8 |

The selected representation adds location context without embedding comments or link
lists. The Santa Teresa/New Mexico query improved from rank 72 with RSS-only text to
rank 1. Tradeoffs remain: the Apple Orchard Mountain dome query ranked 20 with focused
text versus 3 with full evidence, while the Cyprus mountain query improved from 24 to 3.
Both are retained as diagnostic cases in the CLI evaluation suite. All four new named-site
regressions returned their expected result first; all six earlier named-item cases still
passed. Raw HTML and Markdown round trips passed for all three sources.

The initial three-source dataset contained 3,120 entities, 2,632 evidence pages, and 11,172 vectors.
The new `site` filter works alongside existing entity kinds; source and kind filters are
checked by the executable evaluation, and the release workflow runs these checks.

### RussianForces discovery and extraction

The supplied FeedBurner URL is an Atom feed. Its response matched the publisher's
[direct Atom feed](https://russianforces.org/atom.xml) byte for byte, so the adapter uses
the publisher URL as its seed. The feed exposes 15 recent English posts, with both short
summaries and complete HTML content. The publisher also has category, year, and month
archives and a search form with keyword, case-sensitive, and regular-expression controls.
The search endpoint is under `/cgi-bin/`, which robots.txt disallows. The scraper follows
only canonical article URLs from the feed and previously published evidence; it does not
submit searches, enumerate the historical archives, or follow article references.

Direct HTTP supplies the complete article body, tables, source article ID, author, and
publication date. No browser is needed. The September 20, 2026 UTC crawl saved the feed
and all 15 articles with HTTP 200. Every article title and normalized body text matched
its Atom entry. Summaries were only 209-280 characters, compared with 670-7,431 characters
in full articles. Fetching the article also preserves original HTML and citation metadata.

These are analytical reports, so articles become evidence for named items. A reviewed
[name catalog](src/pipelines/sources/russianforces_entities.json) defines 34 equipment
identities and their source-observed aliases. Explicit `Cosmos` designations add 23
satellite identities automatically. Models, classes, and named individual objects remain
separate records. Keys such as `russianforces:razvyazka` are stable catalog keys, while
`russianforces:cosmos-2615` follows the reported designation. Article IDs remain provenance.
Repeated mentions merge under the same entity ID; nine entities currently have multiple
evidence pages. Article headlines, unnamed objects, organizations, and events do not
become item records.

Name boundaries distinguish Voronezh-DM from Voronezh-DM1 and UR-100 from UR-100NUTTH.
Context checks distinguish the submarine Bryansk from a city mention. Only literal Cosmos
numbers are extracted: a range does not invent intervening identities, and temporary
labels such as OBJECT A are not globally unique entity names. Source table rows retain
their reported identifiers and values, including later updates and tentative identities.
The 57 records comprise 26 spacecraft, 13 weapons, five radars, four aircraft, four vessels,
three launch vehicles, and two upper stages.

The catalog is deliberately reviewed rather than unrestricted named-entity recognition.
New equipment names need catalog entries before they become independent search results.
Unrecognized mentions remain in retained article text; a post with no recognized item
stays only in the raw crawl archive. This first snapshot covers the current feed, not the
publisher's entire history. Both feed adapters use the shared
[previous-membership helper](src/pipelines/sources/feeds.py) to retain previously indexed
article/detail URLs on explicit refreshes. Nothing refreshes during consumer CLI use.

Embeddings contain the entity identity plus paragraphs and labeled table rows that mention
it. Every entity still exposes each associated article in full Markdown and byte-exact
HTML, with author, publication date, references, and original qualifications. A report's
speculation or historical observation is not promoted to a verified current capability
or status. Navigation, comment forms, sidebars, and scripts are excluded from extracted
article Markdown. Linked PDFs, KMZ files, imagery, and external references remain links.
Source material retains its attribution and original terms.

Eight source-filtered queries compared full-article and entity-passage embeddings using
the pinned model and pure cosine ranking. This is a small diagnostic, not a general
accuracy estimate:

| Embedding input | Vectors for 57 entities | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| Full article for every mentioned entity | 367 | 4/8 | 7/8 |
| Entity-specific paragraphs and table rows (selected) | 139 | 5/8 | 7/8 |

The Chekhov space-surveillance query improved from rank five to one. The Olenegorsk
status query ranked Voronezh-DM1 second because the same passage discusses Dnepr. Numeric
similarity remains weak: the NORAD 68826 query placed Cosmos-2615 at rank 13 with selected
passages, versus seven with full articles. The named Cosmos query was second in pure
vector mode; hybrid name matching retrieves it first. These cases remain in the CLI
evaluator alongside the existing source regressions. All five new named-item checks and
the ten existing named-item checks returned their expected entity first in the built CLI.

The source archive was re-extracted in a container with networking disabled. Repackaging
unchanged snapshots returned `changed: false` with the same dataset ID. The initial
four-source dataset contained 3,177 entities, 2,647 evidence pages, and 11,311 vectors.
Fixture tests cover feed scope, variant boundaries, table extraction, uncertainty,
multi-article merging, missing metadata, and offline refresh membership. Binary acceptance
checks exercise exact full-evidence exports, source/kind filters, and offline embeddings.

### Wikipedia category discovery and extraction

The `wikipedia` source currently starts at the English **Military radars of China**
category and recursively follows its subcategories. Discovery reads only category-member
lists and category pagination; article references, parent categories, language links,
and ordinary navigation do not expand the crawl. The root lists 41 articles and one
subcategory. That subcategory lists eight articles, adding two distinct member URLs.
The September 20, 2026 UTC crawl saved all 45 pages: two categories and 43 article responses.

Evaluation compared category HTML with the MediaWiki category-members API, and a Type 346
article response with REST HTML. Membership and normalized article text agreed exactly.
Production uses ordinary `/wiki/` HTML, which supplies the full article and page/revision
metadata without a browser. The site's search and API interfaces live under `/w/`, which
the crawler's robots policy excludes. Neither is required for this category. Unsupported
pagination causes a discovery error instead of silently publishing a partial category;
the current two category pages need no pagination.

Category membership is not an entity type. Two reviewed non-item pages, the research
institute and the national missile-warning-system overview, are archived but excluded
from the entity index. Shaanxi KJ-2000 is an aircraft. The Type 1475 Radar member currently
redirects to Chengdu J-20, so that response produces the aircraft entity, with its actual
title and page ID; the old radar title is not added as an aircraft alias. New unrecognized
entity types fail extraction for review. The resulting corpus has 39 radars and two aircraft.

IDs use Wikipedia's numeric page ID, for example `wikipedia:51215241` for Type 346 radar.
Renaming a page does not change the ID. Each entity retains the captured revision ID,
canonical article URL, permanent revision link, contributor-history link, visible
categories, and infobox facts with original units and variant qualifications. A family
article remains one entity; variants described within it remain in the full evidence.
The adapter does not invent separate variant identities from every model number mentioned.

Aliases come from the opening subject description and infobox title. Bold text elsewhere
can name a predecessor or comparison item: JL-10A's mention of Type 232H is retained as
evidence but is not an alias. Observed aliases such as Dragon Eye, Type 1478, Rice Screen,
LLQ302, and Mainring are searchable. Original HTML is exportable byte for byte. Markdown
retains the article body, technical lists/tables, captions, references, and source-quality
notices, while removing navigation and editing controls. Images and other attachments
remain links. Wikipedia contributor attribution, the captured CC BY-SA license link,
revision provenance, and the HTML-to-Markdown transformation are recorded with every page;
linked media retain their own licensing terms.

Embeddings use article prose, specifications, infoboxes, and captions. Reference lists,
bibliographies, maintenance messages, and unrelated navigation stay out of embeddings.
Qualifications such as "reported", "believed", and variant-specific ranges remain in the
source text. Eight distinct queries compared three representations using the pinned
model and pure cosine ranking across the 41 entities:

| Embedding input | Vectors | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| Lead paragraphs only | 93 | 4/8 | 6/8 |
| Full extracted article | 398 | 3/8 | 7/8 |
| Article body without references and maintenance text (selected) | 237 | 4/8 | 7/8 |

The selected representation preserves technical sections that lead-only extraction would
lose, with fewer vectors than full-article embedding. These are small diagnostics, not a
general accuracy estimate. Type 1478 still ranks 11th in pure vector mode; the captured
alias makes it first in hybrid mode. The three capability queries rank their expected
items first, fourth, and fourth. All five new named-item regressions, and all 15 existing
named-item regressions, return the expected entity first. Numeric-designation failures
remain visible as diagnostic cases in the CLI evaluator.

Offline container extraction passed, and unchanged repackaging preserved the dataset ID.
The initial five-source bundle contained 3,218 entities, 2,688 evidence pages, and 11,548
vectors. Tests cover category scope, pagination rejection, stable identity, redirects,
alias exclusions, variant qualifications, legacy and current HTML layouts, missing
provenance, and exact archived bytes. Additional Wikipedia categories can be added as
reviewed seeds in the adapter; review their entity kinds and exclusions before expanding
scope. The shared builder and consumer CLI need no source-specific changes.

### Wikimedia Commons discovery and extraction

The `commons` source recursively follows **Military radars of Russia**, its category
membership lists, and linked file-description pages. The root has 72 subcategories and
47 directly listed files. Evaluation of the complete reachable graph found 196 categories
and 1,374 distinct file-description URLs. Parent categories, search results, user pages,
global navigation, linked Wikipedia articles, and image binaries do not expand the crawl.

Static `/wiki/Category:` and `/wiki/File:` HTML supplies category membership, descriptions,
rendered Wikidata infoboxes, page/revision IDs, and attribution. A browser is unnecessary.
The category offers search, category-tree navigation, WikiMap/KML, PetScan, and a dynamic
"Search depicted" tool; those are not required for this bounded crawl. Robots evaluation
allows the ordinary HTML pages and excludes `/w/api.php` and `/w/index.php` query routes.
Discovery recognizes supported category continuation links and fails on unsupported
pagination rather than silently truncating a collection. None of the 196 evaluated
category pages needed pagination. Structured file data loaded by JavaScript is outside
this adapter's captured content; the raw HTML and rendered description are retained.

Commons organizes media, so photographs do not become equipment identities. The
[reviewed category catalog](src/pipelines/sources/commons_categories.json) distinguishes
151 named subjects, 31 context categories, and 14 broad or ambiguous collections. Subjects
include 134 radars, seven sites, four equipment records, three emitters, two command
vehicles, and one sensor. IDs are the subject category's native numeric page ID, such as
`commons:54320747` for 1L122-2E. Newly encountered category IDs require classification
before publication. The catalog records category roles and types; titles, descriptions,
visible aliases, and evidence come from the archived pages.
The main entity URL selects its subject category even when a context page is processed
first. Shared media descriptions remain independently addressable by evidence ID.

An offline preparation pass resolves the category graph before extracting each page.
Museum, exhibition, and service-context categories attach evidence to their named parent
subject. Distinct named model categories remain separate; if a file belongs to both a
family and its specific variant, the specific variant takes precedence. Files depicting
multiple named subjects can be evidence for each, with identical full content. General
collection files stay in the archive unless membership in a reviewed subject establishes
their association. The mixed "Kasta 2E2 and Kavosh" collection does not establish that all
its photographs depict the same model, so that grouping alone assigns no entity.

The root also contains Soviet equipment, foreign-service collections, museums, and sites
outside Russia. Category membership is retained as qualified provenance; it does not
become an assertion of Russian origin, current location, or operator. Ship photographs
can show several radar systems, and source identifications may be uncertain. Search
results represent the category subject and its supporting pages, not independently
verified object recognition. Category descriptions and file captions retain qualifications.

Full Markdown preserves category descriptions, media descriptions, licenses, source and
author details, file history, and rendered metadata. Original response HTML exports byte
for byte. File dates are explicitly media metadata, not equipment service dates. Every
page records a revision link, contributor-history link, its CC BY-SA page-text license,
and the Markdown conversion. Media license labels and links are separate facts where
present. Media bytes remain external links and keep their individual licenses.

Search text uses equipment identity, English category descriptions and rendered infoboxes,
and English file captions. Older descriptions written as prose or lists are supported.
Full evidence keeps the original multilingual text and is marked `mul`; explicitly
non-English sections are excluded from embeddings, while untagged source captions may
contain mixed languages. Hidden multilingual label caches are not aliases. License text,
file history, EXIF, and navigation stay out of embeddings to avoid repeated boilerplate.

The September 20, 2026 UTC crawl saved all 1,570 discovered pages with HTTP 200 responses.
The published snapshot retains 182 category pages and 1,224 file-description pages as
evidence; 164 collection or unassigned pages remain in the original crawl archive.
Forty-four evidence pages are shared by multiple subjects. Duga-1 retains 198 pages.
All retained file pages supplied media-license metadata. For 235 files without usable
English descriptions, embedding input falls back to the equipment identity while the
original multilingual content remains exportable.

Eight distinct queries compared three representations across all 151 subjects using the
pinned model and pure cosine ranking:

| Embedding input | Vectors | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| Category descriptions only | 333 | 6/8 | 6/8 |
| Full extracted page Markdown | 11,994 | 5/8 | 6/8 |
| Category descriptions and file captions (selected) | 1,680 | 6/8 | 6/8 |

The selected representation keeps caption information that category-only indexing would
omit, with about one-seventh the vectors of full-page indexing. This small diagnostic
set is not a general accuracy estimate. Repeynik ranks seventh in pure vector mode and
first through its captured alias in hybrid mode. A broad query about detecting stealth
aircraft, cruise missiles, and unmanned vehicles places 96L6 at rank 45 despite the source
describing that capability. These misses remain visible in the evaluator. The tracked
air-defense command-vehicle and Moscow missile-defense queries return their expected
items first. All six Commons named-item checks and the 20 existing required checks
return their expected entity first.

Networking-disabled container extraction passed. Repackaging unchanged snapshots preserved
the dataset ID and reported `changed: false`. The initial six-source bundle contained
3,369 entities, 4,094 evidence pages, and 13,228 vectors. Windows acceptance verifies full
exports for each source; an additional check round-trips all 198 Duga-1 evidence pages.
The original `russian cheeseboard` query still returns 96L6E "Cheese Board" first in both
hybrid and vector modes. Source tests cover membership scope, pagination rejection,
context and variant grouping, shared evidence, category review, legacy layouts, English
selection, separate media/text licensing, preferred subject URLs, and offline replay.
All five platform binaries were built. Windows and Linux amd64 passed bundle verification
and export acceptance; Linux ran with networking disabled and a read-only filesystem.
Linux arm64 passed verification under emulation. macOS builds were cross-compiled here;
the release workflow requires native macOS acceptance before publication.

### Army Recognition discovery and extraction

The supplied `/military-products/air/fighter?task=view&id=139` URL displays **Air Defense
Radars**, because its Joomla query parameters select category 139. Its 11 article links
exactly matched the clean Air Defense Radars category URL used as the seed. Removing the
parameters from the fighter URL changes its meaning: that separate aircraft catalog has
12 cards on its first page and four pages of pagination. Only the verified category-139
query combination maps to the radar seed; other queries are rejected.

The military-product taxonomy offers category navigation and article cards, followed by
detail pages with section anchors, galleries, and related products. This category needs
no pagination or interactive filter. Direct HTTP returned complete category and article
HTML, so production requires no browser, site search, or API. Robots permits these paths
but excludes `?start=` pagination. A new pagination link causes a discovery error for
review; the scraper does not rewrite parameters to fetch excluded pages. Related-product
links, other categories, news, images, and external references do not expand this crawl.

The September 20, 2026 UTC crawl saved the category and all 11 detail pages with HTTP 200.
Five articles use legacy nested tables; six use sections named `desc`, `data`, `spec`,
`details`, and `photos`. Extraction supports both. Colored specification rows are paired
by column before layout tables are flattened, so adjacent labels do not acquire each
other's values. Descriptions, variant qualifications, technical sections, specifications,
visible article dates, captions, references, and gallery links remain in full Markdown.
Every original response remains byte-for-byte exportable. Advertisements, shared menus,
related products, counters, and internal navigation are excluded from extracted content.

A [reviewed subject catalog](src/pipelines/sources/armyrecognition_subjects.json) assigns
kinds and source-observed aliases to these pages. The MSP 500 NASAMS article describes an
electro-optical sensor and uses `kind: sensor`; the other ten subjects use `kind: radar`.
Catalog membership does not establish Russian origin. The 92N6 article's slug contains
`96n6`, but its displayed title and aliases identify 92N6/92N6E. Slugs, related products,
carrier vehicles, and comparison systems do not become aliases. Families with several
variants remain one entity per source article. Newly discovered subjects require review
before publication, and catalog/detail title mismatches fail extraction.

Article IDs in JSON-LD are not consistently supplied. All records therefore use the first
24 hexadecimal characters of SHA-256 over their canonical article URL, independent of
optional JSON-LD and view counters. For example, MSP 500 is
`armyrecognition:e4a5aecde1b187a0deffbab3`. IDs survive text and metadata changes; URL renames
require explicit identity review. Canonical URL changes fail extraction rather than
silently assigning an existing record to a different page.

Facts preserve raw values and units, including `?`, approximations, and contradictory
source claims. Numeric normalization is deliberately omitted. For example, the 50N6A
article describes both 8x8 and 6x6 chassis and reports different road-range units in prose
and specifications; both statements remain available. These are attributed source claims,
not independently verified performance data. Copyright and the publisher's
[reuse terms](https://www.armyrecognition.com/legal-information) accompany every evidence
page. The site does not grant an open redistribution license; obtain the necessary rights
before distributing its content in a public dataset or binary. Local scraping and binary
evaluation do not establish those rights.

Eight queries compared three inputs across all 11 entities using the pinned embedding
model and pure cosine ranking, without the hybrid alias boost:

| Embedding input | Vectors | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| Catalog snippets | 22 | 6/8 | 8/8 |
| Full extracted Markdown | 130 | 8/8 | 8/8 |
| Article text without image filenames (selected) | 90 | 8/8 | 8/8 |

The selected input retains full technical prose and specifications without repetitive
image filenames. The Arrow-system radar query improved from fifth with catalog snippets
to first, and the Polish hovering-helicopter query improved from second to first. This
small category-specific diagnostic is not a general accuracy estimate. The committed
retrieval suite checks all 11 named items and four additional capability queries against
the actual CLI, including source and sensor/radar filters.

Repeat HTTP samples and production responses yielded identical extraction for every
article despite changing transport bytes. A container with networking disabled reproduced
the entity catalog and all 11 Markdown files exactly. Tests cover query semantics,
pagination rejection, both article layouts, column pairing, uncertainty, alias boundaries,
incomplete pages, ad/metadata changes, and byte-exact offline publication.

The initial seven-source bundle contained 3,380 entities, 4,105 evidence pages, and 13,318
vectors. All 37 required named-item queries across the seven sources return the expected
item first, including all 11 new items. The four Army Recognition capability diagnostics
also return their expected item first; six previously documented diagnostics in other
sources still miss their thresholds. Windows acceptance round-trips all 11 new entities
and their original HTML/Markdown, and the global `russian cheeseboard` query remains first
in both modes. Networking-disabled repackaging reports `changed: false` with the same
dataset ID. All five platform executables were built locally.
Linux amd64 also passed verification, exact exports, and all 37 required retrieval checks
with networking disabled and a read-only filesystem. Linux arm64 passed verification under emulation. macOS binaries
were cross-compiled locally; the release workflow still requires native macOS acceptance.

### Fandom Military Wiki discovery and extraction

The `fandom` source covers the supplied **Russian and Soviet military radars** category.
On September 20, 2026 UTC, ordinary article/category HTML, `robots.txt`, and the
`index.php?action=render` route returned HTTP 403 challenges. An ordinary agent-browser
session also reached human verification. No challenge was solved or bypassed. The public
`api.php` endpoint independently served HTTP 200 JSON with the normal project user agent,
without cookies or authentication, so production uses that endpoint and needs no browser.
Robots enforcement remains enabled; the robots response was unavailable, not a successfully
read allow policy. The shared crawler's unavailable-robots behavior applied to these API
requests, with its usual delay, throttle, and concurrency limits.

Discovery uses MediaWiki's [categorymembers API](https://www.mediawiki.org/wiki/API:Categorymembers)
with `cmtitle`, `cmlimit=500`, and the server's continuation tokens. The live category listed
53 articles with no subcategories. A second evaluation using ten results per request
followed six pages and returned exactly the same 53 IDs, without duplicates or omissions.
The adapter validates pagination completeness and rejects cycles, unsupported namespaces,
API errors, and unreviewed membership changes. Article references, search results, parent
categories, other wikis, media, and external links never expand this crawl.

The [parse API](https://www.mediawiki.org/wiki/API:Parsing_wikitext) supplies full rendered
article HTML, page IDs, revision IDs, and categories. A query for page information confirmed
that similarly named category entries were separate pages, not redirects. The
[reviewed identity catalog](src/pipelines/sources/fandom_pages.json) groups the Mech/Myech,
Duga/Russian Woodpecker, N019/Rubin, Zaslon, and Zhuk duplicates into 46 subjects while
retaining all 53 articles. Airborne Bars and naval MR-103 Bars/Muff Cob remain separate.
IDs use the preferred article's native page ID, such as `fandom:344747` for Duga and
`fandom:130998` for P-18. New or renamed members require catalog review; changed parse
identities fail rather than silently following a different subject.

The production crawl saved 55 successful responses: category membership, wiki license
metadata, and 53 articles. Extraction retains descriptions, specifications, infobox values,
captions, references, original qualifications, and imported attribution notices. Infobox
facts keep their source units and wording without inferring current operating status or
normalized performance values. Aliases come from the introductory subject names and
reviewed names actually present in the source. Captions, comparisons, and equipment
mentioned later in an article do not become aliases.

Every evidence record links its canonical article, revision, and contributor history.
The captured rights API reports `CC-BY-SA` and links to [Fandom licensing](https://www.fandom.com/licensing);
it supplies no version number. Imported Wikipedia/GFDL notices are retained as written,
and media may have separate terms. The exact JSON capture and exact API HTML fragment
remain exportable. Full Markdown uses stable file-description links in place of thumbnail
delivery URLs, preserving captions and removing layout controls. In repeat evaluation,
P-15's unchanged revision alternated between thumbnails and broken-image notices;
normalizing those links and excluding maintenance categories made all 53 extracted
records stable across the two captures. Original rendering differences remain in the archive.

Embeddings use article prose, technical sections, infoboxes, and captions. References,
license boilerplate, maintenance notices, image filenames, and navigation are omitted
from search text while remaining available in the full evidence. Offline extraction was
verified in a container with networking disabled. Fixture tests cover scope, continuation,
identity review, duplicate grouping, alias exclusions, original response preservation,
derived HTML integrity, transient image failures, and content-change gating.

Eight distinct queries compared representations across the 46 subjects using the pinned
model and pure cosine ranking, with a source filter:

| Embedding input | Vectors | Expected item first | Expected item in first five |
| --- | --- | --- | --- |
| Lead paragraphs only | 124 | 8/8 | 8/8 |
| Full extracted Markdown | 682 | 8/8 | 8/8 |
| Article body without references and boilerplate (selected) | 418 | 8/8 | 8/8 |

The selected representation retains technical sections beyond the introduction, using
fewer vectors than full-page embedding. This small diagnostic does not establish general
accuracy. Queries include Russian Woodpecker, Spoon Rest D, Flash Dance, N001 Mech, and
descriptions of Duga's shortwave interference, Gabala's location, naval gun control, and
the Su-35 radar. Twelve Fandom cases are retained in the CLI evaluator, including separate
Bars/Muff Cob identities and source/kind filters.

The initial eight-source bundle contained 3,426 entities, 4,158 evidence pages, and 13,736
vectors. The 53 original API article captures round-trip independently of their derived
HTML, and full Markdown remains available for every article, including duplicate pages.
All twelve Fandom CLI queries pass, along with all 45 required checks across the eight
sources. Six existing optional semantic cases remain misses in the diagnostic report.
The unfiltered `russian cheeseboard` query still returns 96L6E first in both search modes.
Unchanged repackaging reports `changed: false` and preserves the dataset ID.

Verification included 119 Python tests, lint/type checks, Go tests/vet, and all five binary
builds. Windows and Linux amd64 passed bundle verification and exact export acceptance;
Linux ran with networking disabled and a read-only filesystem. Linux arm64 verification
passed under emulation. macOS binaries were cross-compiled locally; release publication
still requires their native acceptance jobs. API errors returned with HTTP 200 are marked
as failed captures so an explicit crawl retry can fetch them again.

### ClimateViewer Fortress Russia discovery and extraction

The `climateviewer` source imports the supplied [Fortress Russia GeoJSON](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson)
and its [map description and attribution](https://climateviewer.org/history-and-science/government/maps/fortress-russia-air-defence-radar-sam-sites/).
On September 20, 2026 UTC, both resources and `robots.txt` returned HTTP 200 with the
normal project user agent. Robots contained no exclusions. The Cesium map provides layer
and base-map controls, but the complete collection is already available in one static
response: no browser, search requests, pagination, or map interaction is needed. The
production crawl saves those two resources with the shared robots checks and throttling;
external references, photographs, and other map layers never expand its scope.

The 2,989,317-byte collection contains 766 features: 383 Point markers and 383 LineString
overlays. Every Point becomes a `site`: 291 radar sites, 65 SAM sites, 22 air bases, and
five ABM sites. The illustrative range/radius overlays have no explicit parent IDs and
are not consistently adjacent to their markers. They remain in the original collection
without being assigned to sites or interpreted as verified performance data. Equipment
mentioned in a site's description remains site evidence rather than a new equipment
entity. The adapter validates geometry types, finite coordinate values, geographic
bounds, required descriptions, duplicate identities, and the map's provenance.

The map cites **Integrated Air Defence of Russia 2010**, and the layer is hosted under a
2018 path. Neither date establishes when every marker was observed. All records carry
this historical context; descriptions do not establish current deployment or operating
status. Country is not inferred from the layer title. Original uncertainty, approximate
locations, `N/A` values, component lists, site histories, and contributor credits survive
extraction. Longitude and latitude use GeoJSON coordinate order; the complete geometry,
including any third coordinate, remains in the original point record.

No native feature IDs exist. Entity keys hash the source marker name and coordinates,
independently of feature order, descriptions, and styling. Repeated names and distinct
colocated markers remain separate. Renaming or moving a marker changes its ID and needs
identity review if continuity is required. Only the marker's own name becomes an alias;
short numeric names are excluded. The JSON dump contains the entire original Point
feature in `evidence[].records`, including all properties and description HTML. Full
Markdown and generated HTML retain that record alongside readable site text. The exact
original GeoJSON, including all overlays, is stored once and can be exported using any
site's ID with `get --format source`.

The captured map notice credits Jim Lee and specifies
[CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), including its
noncommercial restriction. That notice and the descriptions' original Planeman, Sean
O'Connor/SOC, and other credits accompany the records. Linked media retain their own
rights; media files are not downloaded. These data terms are separate from the CLI's
software license.

Eight queries compared embedding representations across all 383 sites using the pinned
model and pure cosine ranking:

| Embedding input | Vectors | Expected site first | Expected site in first five |
| --- | --- | --- | --- |
| Marker names only | 766 | 5/8 | 5/8 |
| Full extracted Markdown | 1,758 | 8/8 | 8/8 |
| Site descriptions without credits or image URLs (selected) | 766 | 7/8 | 8/8 |

The selected input retains historical and technical descriptions with 56% fewer vectors
than full Markdown, avoiding duplicated raw JSON and map styling. The former MiG-31 air
base query places Khotilovo second; including its name places it first. Full Markdown
performed better on that query. This eight-query diagnostic is not a general accuracy
estimate, and many markers share identical descriptions. The committed CLI evaluation
adds eight required named-site cases and four semantic cases, including approximate
location wording and historical occupancy changes.

Repeat HTTP captures were byte-identical. Offline extraction with networking disabled
preserved all 383 original Point features exactly and retained one complete source
response. Canonical rendering and a fingerprint of every feature make JSON property order,
feature order, and whitespace irrelevant to content identity; changes to a Point or an
unassigned overlay still change the dataset fingerprint. Fixture tests cover malformed
geometry, source scope, identity, uncertainty, license/provenance changes, response and
HTML corruption, offline publication, and content-change gating.

The combined nine-source bundle contains 3,809 entities, 4,541 evidence pages, and 14,502
vectors. Windows and Linux amd64 acceptance round-trip all 383 site records, their generated HTML and
complete Markdown, and the byte-exact original collection. All twelve ClimateViewer
retrieval cases pass, including all eight named sites at rank one. All 53 required cases
across the nine sources pass; six previously documented optional diagnostics remain
misses. The unfiltered `russian cheeseboard` query remains first in both modes.
Networking-disabled repackaging reports `changed: false` with the same dataset ID.
Verification also passed 136 Python tests, lint/type checks, Go tests/vet, and all five
binary builds. Linux amd64 verification and exports also pass with networking disabled
and a read-only filesystem. Linux arm64 verification passed under emulation. macOS binaries were
cross-compiled locally; release publication still requires native macOS acceptance.

### Add another website

Implement the [Source protocol](src/pipelines/sources/base.py) in
`src/pipelines/sources/`, then register its import path in
[src/pipelines/sources.toml](src/pipelines/sources.toml). No website-specific changes are
needed in the shared builder, bundle format, or consumer CLI.

| Adapter member | Responsibility |
| --- | --- |
| `id`, `version` | Source namespace matching registration; discovery compatibility version |
| `seeds`, `minimum_entities` | Initial URLs and a lower bound that protects publication |
| `normalize(url)` | Canonical in-scope URL, or `None`; enforce host, language, and path scope |
| `discover(url, body)` | Links discovered from saved response bytes |
| `labels(url, body)` | Explicit catalog names keyed by canonical item URL, or `{}` |
| `extract(url, body, names)` | Zero or more entities derived from the current archived page |
| `discovery_seeds(archive_directory)` (optional) | Discovery captured once per archive, including rendered catalogs or previous feed membership |
| `prepare(pages)` (optional) | Resolve relationships from an iterable of archived `(url, bytes)` pairs before per-page extraction; no network requests |

Use [Entity and Evidence](src/pipelines/model.py) for extraction. Each emitted entity needs
a stable key, canonical title, kind, and one evidence record for the current
page. The builder attaches retrieval time and the HTML hash. Return `[]` for non-item pages;
extraction must not fetch other pages. Keys are 1-128 ASCII letters/digits/dots/underscores/
hyphens and start with a letter or digit. Prefer source-native catalog IDs over names or
crawl order. For narrative sources without equipment IDs, maintain explicit reviewed
identity keys rather than deriving them from article headlines.

Emit the same key/title/kind from multiple pages only when the source establishes they
refer to the same item. Their evidence, aliases, and facts merge; conflicting identities
fail publication. Several items can share a catalog page, but each must retain identical
full Markdown for it. Prefer item detail pages when available to avoid ambiguous shared
text. Facts point to retained evidence URLs or anchors, and aliases name the item itself.
Set `Evidence.search_text` to an item's own section when shared-page text would confuse
sibling variants. Leave it empty to embed full Markdown. This never replaces the retained
full evidence. An optional `Entity.url` may select an anchor within a retained page.
For an API that returns rendered HTML, set `Evidence.rendered_html` and the human-facing
`Evidence.canonical_url`, keeping `Evidence.url` equal to the captured API request. The
shared builder writes the derived HTML and attaches `html_origin: "api-rendered"` plus
`source_response` with its URL, content type, SHA-256, and exact `body_base64`. Producer
and consumer integrity checks validate both representations. Ordinary HTML adapters
leave these optional fields empty and retain their existing exports.
For structured collections, set a stable `Evidence.record_id`, complete original objects
in `Evidence.records`, and item-specific `rendered_html` and Markdown. Keep `Evidence.url`
equal to the captured collection request. Evidence identity then hashes the URL plus the
record ID, allowing independent records in one response. The builder uses
`html_origin: "record-rendered"` and `source_response.body_member` to refer to one
checksum-verified `responses/SOURCE/URL_HASH.json` bundle member. This supports additional
JSON/GeoJSON sources without duplicating an entire collection inside each entity dump.
Preserve collection-level data changes in the source's semantic fingerprint when excluded
features still belong to its original export, as ClimateViewer does for line overlays.
Implement the separate `SupplementalDiscovery` protocol for discovery state not available
in ordinary archived HTML, such as browser-rendered catalogs or prior feed membership;
offline extraction never invokes that capability.
Implement `PreparedSource` when extraction depends on relationships between archived
pages. Commons uses it to associate media descriptions with equipment categories. The
builder invokes it for both fresh crawls and offline replay; ordinary adapters need no
preparation method. Keep this pass deterministic and confined to the supplied archive.

Supported kinds are `radar`, `emitter`, `sensor`, `vehicle`, `aircraft`, `spacecraft`, `vessel`, `weapon`, `site`,
`equipment`, and `item`. Categories provide source-specific distinctions. Country and
manufacturer facts need source evidence; cross-source entity resolution and attachment
OCR are not implemented.

Add fixtures covering URL scope, identity, exclusion of non-items, extraction fidelity,
and any catalog/multi-page behavior. [The independent catalog fixture](tests/test_sources.py)
already exercises vehicles and emitters alongside radar data, shared evidence, and
multi-page entities. It is a test adapter, not a production website. Bump the
adapter version when discovery changes make an unfinished crawl unsafe to resume.

## CLI API and installation

Download the executable for your platform from
[GitHub Releases](https://github.com/osint-builders/pipelines/releases). If no release is
available yet, build locally using the commands above.

| Platform | Release asset |
| --- | --- |
| Linux x86-64 / ARM64 | `pipelines-linux-amd64` / `pipelines-linux-arm64` |
| macOS Intel / Apple Silicon | `pipelines-darwin-amd64` / `pipelines-darwin-arm64` |
| Windows x86-64 | `pipelines-windows-amd64.exe` |

Check the download against `SHA256SUMS`, rename it to `pipelines` (`pipelines.exe` on
Windows), and place it on your PATH. On Linux/macOS, run `chmod +x pipelines`. No separate
dataset, service, API key, or first-run model download is required.

| Command | Result |
| --- | --- |
| `pipelines search [--mode hybrid\|vector] [--limit N] [filters] "query"` | Ranked, deduplicated entities |
| `pipelines similar [--limit N] [filters] SOURCE:ID` | Nearest entities, excluding the input entity |
| `pipelines get [--format json\|markdown\|html\|source] [--evidence PAGE_ID] SOURCE:ID` | Complete entity/evidence export |
| `pipelines info` | Dataset identity, counts, model, and included sources |
| `pipelines verify` | Bundle integrity and cross-runtime embedding checks |
| `pipelines version` | Executable version |
| `pipelines notices` | Model and dependency license notices |
| `pipelines --help` | Command usage |

Filters are `--source SOURCE`, `--kind KIND`, and `--category CATEGORY`; they apply before
the result limit. The default limit is 10, with a range of 1-100. Place flags before the
quoted query or ID. Queries are limited to 1,000 characters and 256 model tokens. There
is currently no structured country or numeric-fact filter.

```sh
pipelines search "russian cheeseboard"
pipelines search --source deagel "M142 HIMARS wheeled rocket artillery launcher"
pipelines search --source virtualglobetrotting --kind site "Bullen Point Alaska radar"
pipelines search --source russianforces --kind radar "Razvyazka space surveillance radar"
pipelines search --source russianforces --kind spacecraft "Cosmos 2615"
pipelines search --source wikipedia --kind radar "Dragon Eye"
pipelines search --source commons --kind radar "1L122-2E"
pipelines search --source armyrecognition --kind sensor "MSP500 NASAMS"
pipelines search --source fandom --kind radar "Russian Woodpecker"
pipelines search --source climateviewer --kind site "BOLSHOYE SAVINO air base"
pipelines search --mode vector --kind radar "detect aircraft approaching an airport"
pipelines similar --limit 5 radartutorial:8bdc6ce92fea3ca62de71395
pipelines get radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format markdown radartutorial:8bdc6ce92fea3ca62de71395
pipelines get --format html radartutorial:8bdc6ce92fea3ca62de71395
pipelines get deagel:a000516-003
pipelines get virtualglobetrotting:311208
pipelines get russianforces:sineva
pipelines get wikipedia:51215241
pipelines get commons:54320747
pipelines get armyrecognition:e4a5aecde1b187a0deffbab3
pipelines get fandom:344747
pipelines get climateviewer:fortress-russia-308b83193fb2a0e18ede7fbd
pipelines get --format source climateviewer:fortress-russia-308b83193fb2a0e18ede7fbd > fortress-russia.geojson
```

Search emits JSON with `dataset_id`, query/mode, and `results`. Each result includes its
stable `id`, title, source URL, kind, categories, aliases, `score`, raw `cosine`,
`name_match`, snippet, and `evidence_id`. An empty evidence ID denotes an identity match.
Hybrid mode adds a name/alias boost; vector mode ranks solely by cosine. `similar` uses
the selected entity's identity vector. Scores indicate retrieval similarity, not verified
attributes or confidence. The current dataset returns 96L6E "Cheese Board" first for
`russian cheeseboard` in both modes.

`get` defaults to JSON with the complete entity and all evidence pages, including full
Markdown, provenance, and HTML. Non-UTF-8 HTML is represented by `html_base64`. Markdown
output joins all retained pages; `--evidence PAGE_ID` selects one. HTML output preserves
captured HTML bytes for ordinary pages, or the derived article document for API sources,
and requires an evidence ID when the entity has multiple pages. API evidence is marked
`html_origin: "api-rendered"`; JSON output also includes the exact original response in
`source_response.body_base64`, its content type and checksum, and `canonical_url`.
Structured records instead use `html_origin: "record-rendered"`, complete original objects
in `records`, and a `source_response.body_member` descriptor. `--format source` exports
the byte-exact captured response: ordinary HTML, API JSON, or the whole shared GeoJSON
collection. Like HTML output, source output requires `--evidence PAGE_ID` for entities
with multiple evidence pages. This keeps ordinary record dumps compact while retaining
the entire source response for inspection.
Commands return nonzero on failure with a JSON `error` on stderr. All consumer operations
work offline and never start a crawl or update the dataset.
