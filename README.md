# pipelines

Download the [CLI with the image dataset](https://github.com/osint-builders/pipelines/releases/tag/cli-f32652c22c5ed994179356d7800b4537353dbec4fdb91924c6b44fe3b8de3a6b)
for Windows (amd64), Linux (amd64/arm64), or macOS (Intel/Apple silicon).
Extract the platform `.zip` or `.tar.xz`, add `pipelines` (`pipelines.exe` on Windows)
to PATH, and run `pipelines verify`. Each archive contains one executable with the
dataset, models, evidence, and selected image previews. `SHA256SUMS` contains download
checksums; `dataset-manifest.json` describes the bundled data.

This is a **prerelease**: offline text search, facts, filters, comparisons, media
inspection, and image-query suggestions are available. Image identification remains
experimental and uncalibrated; M10 acceptance remains incomplete. CLI downloads are
approximately 385–398 MiB, above the planned compact-release budget. Generated OCR and
descriptions are not included. `pipelines info` reports the exact capabilities and
counts. The [stable text-only CLI](https://github.com/osint-builders/pipelines/releases/latest)
remains available.

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
| `--image PATH` | `search` | Local JPEG/PNG, up to 20 MiB and 40 million pixels; incompatible with `--mode` |
| `--id MEDIA_ID`, `--output PATH` | `media` | Select a record; `--output` requires `--id` and writes its preview to a new file |
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
pipelines list --source odin --where "origin_country=United States" --limit 5
pipelines get --format markdown odin:0a50e596d1ad19fa32b0521d94bb31a8
pipelines facts odin:0a50e596d1ad19fa32b0521d94bb31a8
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
ranked suggestions.

Image queries also return `query_image_sha256` and `calibration_status`. This
prerelease returns `no_supported_match` and `uncalibrated` for image suggestions.
`media` returns an array with original URLs, hashes, dimensions, captions, evidence
references, ambiguity flags, and optional preview metadata. `--output` exports the
preview and returns its byte count and checksum; the full originals are in the
separate image archives.

`similar` returns `similar_to`, `mode`, and `results`. `list` returns `total` and
`results`; the limit applies to the latter. `get` returns the entity, source facts,
and evidence. `facts` returns `entity_id` and `claims`; `relationships` returns
`entity_id` and `relationships`; `compare` returns `entity_ids` and `fields`.
Claims preserve their raw values, parsing status, and evidence alongside parsed
values. Missing comparison values are explicit unknowns.

## Sources and counts

The prerelease above contains **8,463 entities**, **9,595 evidence pages**,
and **269,028 source facts** from 12 sources. Search indexes
53,930 text chunks and 18,584 source captions. Research includes 49,504 indexed
claims across 29 fields and 13 relationships. The CLI includes 3,207 image vectors
with embedded previews covering 3,393 entities.

The downloadable image dataset contains **21,882 saved image URLs** and
**21,751 distinct original files** (5.48 GB).
All 393 Cambridge Pixel entries were reviewed individually: 379 have matched imagery,
including 31 explicitly ambiguous family/configuration associations; 14 remain
unresolved. These matches use 368 saved URLs, with no failed or pending downloads.
The search gallery covers 376 Cambridge Pixel records; three GIF originals are
included in the image download and remain outside image search.

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
| **Total** | **8,463** | **9,595** | **21,882** | |
