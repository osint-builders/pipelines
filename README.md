# pipelines

Download the [latest CLI](https://github.com/osint-builders/pipelines/releases/latest)
for Windows (amd64), Linux (amd64/arm64), or macOS (Intel/Apple silicon).
Extract `pipelines` (`pipelines.exe` on Windows), add it to your PATH, and run
`pipelines verify`. Archives contain one executable with its dataset and models.
`SHA256SUMS` lists download checksums.

The current public release supports text search. Image, observation, and research
commands below require a newer local build whose `info` lists those capabilities.

All commands work offline. `pipelines info` reports the capabilities, sources, field
catalog, counts, and model versions included in your download.

## CLI

Place options before the query or IDs. Use source-qualified IDs returned by a search.

| Command | Output |
| --- | --- |
| `pipelines search [options] "query"` | Text search results |
| `pipelines search --image FILE [options] ["query"]` | Image or combined image/text results |
| `pipelines similar [options] SOURCE:ID` | Similar entities |
| `pipelines list [options]` | Entities matching filters, sorted by ID |
| `pipelines get [options] SOURCE:ID` | Entity and archived evidence |
| `pipelines media [--id MEDIA_ID] [--output FILE] SOURCE:ID` | Media metadata, or an embedded preview written to a new file |
| `pipelines observations [--id OBSERVATION_ID] SOURCE:ID` | Generated OCR/descriptions and their processing recipes |
| `pipelines facts SOURCE:ID` | Source claims with normalized values and evidence |
| `pipelines relationships [--type TYPE] SOURCE:ID` | Evidence-backed relationships |
| `pipelines compare SOURCE:ID OTHER:ID [...]` | Field comparisons for 2–20 distinct entities |
| `pipelines info`, `pipelines version`, `pipelines verify` | Dataset details, executable version, or integrity/model checks |
| `pipelines --help` | Help; also available with `pipelines COMMAND --help` |

| Option | Commands | Meaning |
| --- | --- | --- |
| `--mode hybrid\|vector` | `search` | Names, source captions, and semantic ranking (default `hybrid`), or semantic ranking only; unavailable with `--image` |
| `--observations` | `search` | Include generated OCR/descriptions; requires text |
| `--limit N` | `search`, `similar`, `list` | 1–100 results, default 10 |
| `--source SOURCE`, `--kind KIND`, `--category CATEGORY` | `search`, `similar`, `list` | Filter before ranking or limiting |
| `--where "FIELD OP VALUE"` | `search`, `similar`, `list` | Repeat to require every predicate; operators: `=`, `!=`, `<`, `<=`, `>`, `>=` |
| `--format json\|markdown\|html\|source` | `get` | Default `json`; `source` preserves captured response bytes |
| `--evidence PAGE_ID` | `get` | Select one page; required for HTML/source export when several pages exist |

Image input accepts one local JPEG/PNG, up to 20 MiB and 40 million pixels. Text allows
1,000 characters and 256 model tokens. Numeric filters require compatible units except
for counts. Unknown, approximate, or unparsed values do not satisfy strict filters.
Use `info` for field names. Relationship types are `equivalent`, `family_member_of`,
`variant_of`, `component_of`, and `related_system`.

```sh
pipelines search --source deagel --limit 5 "M142 HIMARS"
pipelines search --image equipment.jpg "tracked vehicle"
pipelines search --observations "warning label"
pipelines list --where "manufacturer=Thales" --where "mass>=10 t"
pipelines get --format markdown radartutorial:8bdc6ce92fea3ca62de71395
```

## API

Run the executable as a subprocess and parse JSON on stdout. Errors return a nonzero
exit code and `{"error":"message"}` on stderr. Help and non-JSON `get` formats return
text or captured bytes.

`search` returns `dataset_id`, `query`, `query_type`, `mode`, `match_status`, and
`results`; image queries also return `query_image_sha256`. Results retain `id`, `title`,
`url`, `source`, `kind`, `categories`, `aliases`, `score`, `cosine`, `name_match`,
`snippet`, and `evidence_id`. `matches` links text/image/OCR/description contributions
to evidence and media IDs; generated matches include recipe identity. Image-only
results use `cosine: null`; their image score is in `matches`. Scores are ranking
signals, not probabilities.

`match_status: no_supported_match` can include suggestions. Image and generated-text
modes return this status unless the bundle contains calibration for the query mode
and exact filtered entity pool. Calibrated responses include `calibration_status:
calibrated` and `decision` with the artifact hash, profile, reason, and measured
signals/thresholds. Current local datasets have no fitted calibration.
`similar` returns `similar_to`, `mode`, and `results`. `list` returns `total` and
`results`; the limit applies to the latter. `get` returns the original entity and
its evidence. `facts`, `relationships`, and `observations` return an `entity_id`
with the corresponding records; `compare` returns `entity_ids` and `fields`.
Claims retain their original values, units, qualifiers, and evidence; missing
comparison values are explicit unknowns. Every entity keeps its source-qualified ID.

## Sources

Use `pipelines info` to see which sources are included in your binary.

| Source (`--source`) | Description |
| --- | --- |
| [radartutorial](https://www.radartutorial.eu/index.en.html) | English radar and equipment pages |
| [deagel](https://www.deagel.com/Armies/) | Military equipment families and variants |
| [virtualglobetrotting](https://virtualglobetrotting.com/category/buildings/radar-sites/rss.xml) | Radar-site feed and linked records |
| [russianforces](https://russianforces.org/atom.xml) | Russian strategic forces articles and equipment |
| [wikipedia](https://en.wikipedia.org/wiki/Category:Military_radars_of_China) | English military-radar category articles |
| [commons](https://commons.wikimedia.org/wiki/Category:Military_radars_of_Russia) | Equipment categories and file descriptions |
| [armyrecognition](https://www.armyrecognition.com/military-products/army/radars/air-defense-radars) | Air-defense radar product pages |
| [fandom](https://military-history.fandom.com/wiki/Category:Russian_and_Soviet_military_radars) | Military Wiki category membership and articles |
| [climateviewer](https://climateviewer.org/layers/geojson/2018/Fortress-Russia-SAM-Sites-ClimateViewer-3D.geojson) | Site records from the Fortress Russia GeoJSON |
| [cambridgepixel](https://cambridgepixel.com/resources/radar-database/) | Radar database records |
| [militaryperiscope](https://militaryperiscope.com/) | Weapons, armed forces, defense companies, and militant organizations |
| [odin](https://odin.t2com.army.mil/WEG/List) | Worldwide Equipment Guide records, specifications, equipment types, and pictures |
