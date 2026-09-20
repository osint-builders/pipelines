# pipelines

Scrape equipment websites and distribute their data as a single offline search CLI.
Users can find radars, emitters, vehicles, sites, and other items by name or meaning, follow a
stable entity ID, and retrieve the complete scraped evidence behind that item.

The executable embeds the dataset, original HTML, Markdown, vectors, and query embedding
model. It requires no API key, Python installation, model download, or writable cache.
The Python scraper and release tools run separately from consuming applications.

## Scraper project

```mermaid
flowchart LR
    A[Explicit crawl] --> B[Original HTML archive]
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
uv run --no-sync pipeline-build status radartutorial --root ../pipeline-data
uv run --no-sync pipeline-build model --output build/model
uv run --no-sync pipeline-build package --root ../pipeline-data --source radartutorial --source deagel --source virtualglobetrotting --source russianforces --source wikipedia --source commons --model build/model --cache build/entity-vector-cache --output build/dataset.zip
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

Source storage is `DATA_ROOT/SOURCE/archives/CRAWL_ID/` for original HTML and a SQLite crawl
manifest, and `DATA_ROOT/SOURCE/published/snapshots/SNAPSHOT_ID/` for `entities.jsonl`, full
Markdown by evidence ID, and snapshot metadata. `published/current.json` selects the
current snapshot. The archive database is producer bookkeeping; runtime lookup uses the
embedded entity catalog and vectors.

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
raw HTML hashes. Repeated Deagel responses contain changing Blazor transport bytes even
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
the dataset ID and reported `changed: false`. The combined six-source bundle contains
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
| `pipelines get [--format json\|markdown\|html] [--evidence PAGE_ID] SOURCE:ID` | Complete entity/evidence export |
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
original response bytes and requires an evidence ID when the entity has multiple pages.
Commands return nonzero on failure with a JSON `error` on stderr. All consumer operations
work offline and never start a crawl or update the dataset.
