# pipelines

Search equipment by name or meaning and retrieve its archived source pages, fully offline.

Download the [latest CLI](https://github.com/osint-builders/pipelines/releases/latest)
for Windows, Linux, or macOS. Extract `pipelines` (`pipelines.exe` on Windows) and
add it to your PATH. The single executable includes the model and dataset.

## CLI

| Command | Description |
| --- | --- |
| `pipelines search [options] "query"` | Search by name or meaning |
| `pipelines similar [options] SOURCE:ID` | Find entities similar to an existing entity |
| `pipelines get [options] SOURCE:ID` | Retrieve an entity and its archived pages |
| `pipelines info` | Show the bundled sources, counts, and model |
| `pipelines version` | Show the executable version |
| `pipelines verify` | Check dataset integrity and model output |
| `pipelines --help` | Show help; also available as `pipelines COMMAND --help` |

Place options before the query or ID. Use IDs returned by `search` or `similar`.

| Option | Commands | Description |
| --- | --- | --- |
| `--mode hybrid\|vector` | `search` | Name and semantic matching (`hybrid`, default), or semantic matching only (`vector`) |
| `--limit N` | `search`, `similar` | Maximum results: 1–100, default 10 |
| `--source SOURCE`, `--kind KIND`, `--category CATEGORY` | `search`, `similar` | Filter results before applying the limit |
| `--format json\|markdown\|html\|source` | `get` | Output format; default `json` |
| `--evidence PAGE_ID` | `get` | Select one archived page |

```sh
pipelines search --source deagel --limit 5 "M142 HIMARS"
pipelines search --mode vector --kind radar "detect aircraft approaching an airport"
pipelines get --format markdown radartutorial:8bdc6ce92fea3ca62de71395
```

## API

Call the executable with arguments and read JSON from stdout. Errors return a
nonzero exit code and `{"error":"message"}` on stderr.

`search` returns `dataset_id`, `query`, `mode`, and `results`. Each result contains
`id`, `title`, `url`, `source`, `kind`, `categories`, `aliases`, `score`, `cosine`,
`name_match`, `snippet`, and `evidence_id`. `similar` returns the same result fields
with `similar_to` in place of `query`. An empty `evidence_id` means the match came
from the entity's identity. Queries allow up to 1,000 characters and 256 model tokens.

| `get` format | Output |
| --- | --- |
| `json` | Entity and evidence, including metadata, Markdown, and HTML |
| `markdown` | All archived pages, or the page selected with `--evidence` |
| `html` | Original HTML or rendered record; select `--evidence` when multiple pages exist |
| `source` | Exact captured response; select `--evidence` when multiple pages exist |

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
| [militaryperiscope](docs/sources/militaryperiscope.md) | Weapons, armed forces, defense companies, and militant organizations |
