# pipelines

Download the [latest CLI](https://github.com/osint-builders/pipelines/releases/latest)
for Windows (amd64), Linux (amd64/arm64), or macOS (Intel/Apple silicon).
Extract the platform `.zip` or `.tar.xz`, add `pipelines` (`pipelines.exe` on Windows)
to PATH, and run `pipelines verify`. Each archive contains one executable with the
dataset, models, evidence, and selected image previews. `SHA256SUMS` contains download
checksums; `dataset-manifest.json` describes the bundled data.

The dataset includes 598 [SigIDWiki signal references](docs/sigidwiki.md), with
structured frequency, bandwidth, modulation, and status filters, plus waterfall
images. Signal references support offline research; they do not decode recordings
or identify transmitters.

The executable includes offline text, image, and combined search, precomputed OCR and
visual descriptions, source evidence, and selected image previews. No runtime,
model download, or separate data file is required. `pipelines info` reports exact
capabilities and counts. Image and generated-text results are **uncalibrated research
suggestions**; inspect the linked evidence. The release's `quality.json` reports the
frozen benchmark and original unmet identification targets; `validation-*.json`
contains native checks.

For original images, download all `images-*.zip` parts, `image-records.json`, and
`image-dataset.json` from the same release. Extract the parts into one directory.
`image-records.json` maps each source URL and entity/evidence reference to a SHA-256;
the original file is `objects/<first two hash characters>/<full hash>`.
`image-dataset.json` binds these records and archive checksums to the CLI dataset.

## CLI

Place options before the query or IDs. Use source-qualified IDs returned by a search.

| Command | Output |
| --- | --- |
| `pipelines search [options] "query"` | Ranked text search results |
| `pipelines search --image PATH [options] ["query"]` | Image or combined image-and-text suggestions |
| `pipelines media [--id MEDIA_ID] [--output PATH] SOURCE:ID` | Image metadata or one embedded JPEG preview |
| `pipelines observations [--id OBSERVATION_ID] SOURCE:ID` | Generated text, image references, and processing recipes |
| `pipelines similar [options] SOURCE:ID` | Semantically similar entities |
| `pipelines list [options]` | Entities matching filters, sorted by ID |
| `pipelines get [options] SOURCE:ID` | Entity, original source facts, and archived evidence |
| `pipelines facts SOURCE:ID` | Indexed claims, parsed values, and source evidence |
| `pipelines relationships [--type TYPE] SOURCE:ID` | Evidence-backed relationships |
| `pipelines compare SOURCE:ID OTHER:ID [...]` | Field comparisons for 2–20 distinct entities |
| `pipelines info`, `pipelines version`, `pipelines verify` | Dataset details, executable version, or integrity/model checks |
| `pipelines --help` | Help; also available with `pipelines COMMAND --help` |

| Option | Commands | Meaning |
| --- | --- | --- |
| `--mode hybrid\|vector` | `search` | Names, source captions, verified numeric clauses, and semantic ranking (default `hybrid`), or semantic ranking only |
| `--observations` | `search` | Include generated OCR/descriptions; requires a text query and can be combined with an image |
| `--image PATH` | `search` | Local JPEG/PNG, up to 20 MiB and 40 million pixels; incompatible with `--mode` |
| `--id MEDIA_ID`, `--output PATH` | `media` | Select a record; `--output` requires `--id` and writes its preview to a new file |
| `--id OBSERVATION_ID` | `observations` | Inspect one generated record and its recipe |
| `--limit N` | `search`, `similar`, `list` | 1–100 results, default 10 |
| `--source SOURCE`, `--kind KIND`, `--category CATEGORY` | `search`, `similar`, `list` | Filter before ranking or limiting |
| `--where "FIELD OP VALUE"` | `search`, `similar`, `list` | Repeat to require every predicate; operators: `=`, `!=`, `<`, `<=`, `>`, `>=` |
| `--format json\|markdown\|html\|source` | `get` | Default `json`; `source` preserves captured response bytes |
| `--evidence PAGE_ID` | `get` | Select one page; required for HTML/source export when several pages exist |

Text queries allow 1,000 characters and 256 model tokens. Structured filters use
indexed claims; unknown, approximate, or unparsed values do not satisfy strict
filters. Numeric filters require compatible units except for counts. Use `info`
for field names and `get` for all original specifications. Relationship types are
`equivalent`, `family_member_of`, `variant_of`, `component_of`, and `related_system`.

```sh
pipelines search --source odin --limit 5 "HIMARS"
pipelines search --image radar.jpg --source cambridgepixel --limit 5
pipelines search "range: 550 km; weight: 54 t"
pipelines search --observations "yellow helicopter with landing skids"
pipelines list --source odin --where "origin_country=United States" --limit 5
pipelines get --format markdown odin:0a50e596d1ad19fa32b0521d94bb31a8
pipelines facts odin:0a50e596d1ad19fa32b0521d94bb31a8
pipelines search --source sigidwiki "Automatic Identification System"
pipelines list --kind signal --where "modulation=FMCW" --where "bandwidth<=50 kHz"
pipelines search --source sigidwiki --image waterfall.png
```

## API

Run the executable as a subprocess and parse JSON on stdout. Errors return a nonzero
exit code and `{"error":"message"}` on stderr. Help and non-JSON `get` formats return
text or captured bytes. Search responses include `dataset_id` so callers can identify
the data used.

`search` returns `query`, `query_type`, `mode`, `match_status`, and `results`.
Results include `id`, `title`, `url`, `source`, `kind`, `categories`, `aliases`,
`score`, `cosine`, `name_match`, `snippet`, and `evidence_id`. `matches` explains
lexical, semantic, specification, and image contributions and links them to evidence.
Scores are ranking signals, not probabilities. `no_supported_match` may still include
ranked suggestions. `candidates` means results were ranked; it does not establish
that the queried entity is present in the archive.

Image queries also return `query_image_sha256` and `calibration_status`. Image,
combined, and generated-text queries return `no_supported_match` and `uncalibrated`.
`media` returns an array with original URLs, hashes, dimensions, captions, evidence
references, ambiguity flags, and optional preview metadata. `--output` exports the
preview and returns its byte count and checksum; the full originals are in the
separate image archives.

`observations` returns `dataset_id`, `entity_id`, `observations`, and `recipes`.
Generated records include `kind`, `origin: "generated"`, text, original image hashes,
source references, and a processing-recipe hash. OCR records also carry text regions.
Generated text can contain errors; it is excluded from default search and source facts.
Search `matches` identifies contributions from OCR and descriptions.

`similar` returns `similar_to`, `mode`, and `results`. `list` returns `total` and
`results`; the limit applies to the latter. `get` returns the entity, source facts,
and evidence. `facts` returns `entity_id` and `claims`; `relationships` returns
`entity_id` and `relationships`; `compare` returns `entity_ids` and `fields`.
Claims preserve their raw values, parsing status, and evidence alongside parsed
values. Missing comparison values are explicit unknowns.

## Sources and counts

The latest release contains **9,061 entities**, **10,193 evidence pages**,
and **273,985 source facts** from 13 sources. Search indexes
55,128 text chunks and 19,165 source captions. Research includes 53,906 indexed
claims across 35 fields and 13 relationships. The CLI includes 9,942 image vectors
with embedded previews covering 8,171 entities. It also includes 12,570
generated records (9,942 descriptions and 2,628 OCR records) in
12,602 searchable chunks.

The downloadable image dataset contains **22,462 saved image URLs** and
**22,330 distinct original files** (5.60 GB). The coverage report also preserves
68 unsuccessful source image URLs, including unsupported formats and unavailable images.
All 393 Cambridge Pixel entries were reviewed individually: 379 have matched imagery,
including 31 explicitly ambiguous family/configuration associations; 14 remain
unresolved. These matches use 368 saved URLs, with no failed or pending downloads.
The search gallery covers 376 Cambridge Pixel records; three GIF originals are
included in the image download and remain outside image search.

SigIDWiki contributes 579 sample-image URLs associated with 581 signals, including
572 distinct searchable JPEG/PNG samples. Six GIF originals are included in the
image download. Seventeen signals have no waterfall sample; the shared placeholder
is excluded from search. All previously indexed images and source captions are
retained. Frequency spans describe reported ranges, not individual occupied channels.

| Source (`--source`) | Entities | Evidence pages | Saved image URLs | Description |
| --- | ---: | ---: | ---: | --- |
| [radartutorial](https://www.radartutorial.eu/index.en.html) | 1,735 | 1,735 | 3,745 | English radar and equipment pages |
| [deagel](https://www.deagel.com/Armies/) | 1,285 | 797 | 2,615 | Military equipment families and variants |
| [virtualglobetrotting](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml) | 100 | 100 | 204 | Radar-site feed and linked records |
| [russianforces](https://russianforces.org/atom.xml) | 57 | 15 | 16 | Russian strategic forces articles and equipment |
| [wikipedia](https://en.wikipedia.org/wiki/Category:Military_radars_of_China) | 41 | 41 | 31 | English military-radar category articles |
| [commons](https://commons.wikimedia.org/wiki/Category:Military_radars_of_Russia) | 151 | 1,406 | 2,372 | Equipment categories and file descriptions |
| [armyrecognition](https://www.armyrecognition.com/military-products/army/radars/air-defense-radars) | 11 | 11 | 241 | Air-defense radar product pages |
| [fandom](https://military-history.fandom.com/wiki/Category:Russian_and_Soviet_military_radars) | 46 | 53 | 45 | Military Wiki category membership and articles |
| [climateviewer](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson) | 383 | 383 | 0 | Site records from the Fortress Russia GeoJSON |
| [cambridgepixel](https://cambridgepixel.com/resources/radar-database/) | 393 | 772 | 368 | Radar records, specifications, and individually reviewed imagery |
| [militaryperiscope](https://militaryperiscope.com/) | 143 | 164 | 427 | Weapons, armed forces, defense companies, and militant organizations |
| [odin](https://odin.t2com.army.mil/WEG/List) | 4,118 | 4,118 | 11,818 | Worldwide Equipment Guide records and specifications |
| [sigidwiki](https://www.sigidwiki.com/wiki/Database) | 598 | 598 | 580 | Signal table records and waterfall images; includes one archived placeholder excluded from search |
| **Total** | **9,061** | **10,193** | **22,462** | |
