# pipelines

Download the [latest CLI](https://github.com/osint-builders/pipelines/releases/latest)
for Windows (amd64), Linux (amd64/arm64), or macOS (Intel/Apple silicon).
Extract the `.zip` or `.tar.xz`, add `pipelines` (`pipelines.exe` on Windows) to your
PATH, and run `pipelines verify`. Each archive contains one executable with the
dataset and text model. `SHA256SUMS` contains download checksums;
`dataset-manifest.json` describes the bundled data.

This release supports offline text search, structured filters, evidence exports,
facts, relationships, and comparisons. `pipelines info` reports the capabilities,
sources, field catalog, counts, and model version in your download. Original images
are stored in the local archive; this release has no embedded images, image search,
or generated OCR/descriptions.

## CLI

Place options before the query or IDs. Use source-qualified IDs returned by a search.

| Command | Output |
| --- | --- |
| `pipelines search [options] "query"` | Ranked text search results |
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
| `--mode hybrid\|vector` | `search` | Names, source captions, and semantic ranking (default `hybrid`), or semantic ranking only |
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
pipelines list --source odin --where "origin_country=United States" --limit 5
pipelines get --format markdown odin:0a50e596d1ad19fa32b0521d94bb31a8
pipelines facts odin:0a50e596d1ad19fa32b0521d94bb31a8
```

## API

Run the executable as a subprocess and parse JSON on stdout. Errors return a nonzero
exit code and `{"error":"message"}` on stderr. Help and non-JSON `get` formats return
text or captured bytes. Data responses include `dataset_id` so callers can identify the
data used.

`search` returns `query`, `query_type`, `mode`, `match_status`, and `results`.
Results include `id`, `title`, `url`, `source`, `kind`, `categories`, `aliases`,
`score`, `cosine`, `name_match`, `snippet`, and `evidence_id`. `matches` explains
lexical and semantic contributions and links them to evidence. Scores are ranking
signals, not probabilities. `no_supported_match` may still include ranked suggestions.

`similar` returns `similar_to`, `mode`, and `results`. `list` returns `total` and
`results`; the limit applies to the latter. `get` returns the entity, source facts,
and evidence. `facts` returns `entity_id` and `claims`; `relationships` returns
`entity_id` and `relationships`; `compare` returns `entity_ids` and `fields`.
Claims preserve their raw values, parsing status, and evidence alongside parsed
values. Missing comparison values are explicit unknowns.

## Sources and counts

The bundled dataset contains **8,455 entities**, **9,208 evidence pages**, and
**268,972 source facts** from 12 sources. Search indexes 53,535 text chunks and
18,205 source captions. Research includes 49,093 indexed claims across 29 fields
and 13 relationships.

The current local archives contain 21,514 saved image URLs representing 21,391
distinct original files (5.32 GB). Other image outcomes: 68 failed, 9,283 excluded,
348 unassociated, and 0 pending. The image counts below describe those local
archives; image files are separate from the CLI download.

| Source (`--source`) | Entities | Evidence pages | Saved image URLs (local) | Description |
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
| [cambridgepixel](https://cambridgepixel.com/resources/radar-database/) | 385 | 385 | 0 | Radar database records |
| [militaryperiscope](https://militaryperiscope.com/) | 143 | 164 | 427 | Weapons, armed forces, defense companies, and militant organizations |
| [odin](https://odin.t2com.army.mil/WEG/List) | 4,118 | 4,118 | 11,818 | Worldwide Equipment Guide records and specifications |
| **Total** | **8,455** | **9,208** | **21,514** | |
