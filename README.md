# pipelines

Offline search across the complete dataset. One executable contains the models,
indices, saved records, and selected image previews.

```sh
./pipeline search "airborne radar"
./pipeline search "airborne radar" --mode vector
./pipeline search "airborne radar" --page 2 --limit 20
./pipeline search "airborne radar" --raw --limit 3
./pipeline search --image radar.jpg
./pipeline search "coastal radar" --image radar.jpg
```

Search covers every source by default. `hybrid` combines semantic similarity with
keyword and name matching; `vector` uses semantic cosine similarity alone. An image
query searches the indexed image gallery. Adding text combines both rankings.
Image similarity is a research suggestion, not verified identity.

## Search options

Options work before or after the query. Quotes are optional for multiple query
words; use `--` before literal query words that begin with a dash.

| Option | Meaning |
| --- | --- |
| `--mode hybrid\|vector` | Text ranking; default `hybrid` |
| `--image PATH` | Search with a local JPEG or PNG |
| `--page N` | Page number, starting at 1; default 1 |
| `--limit N` | Results per page, 1–100; default 10 |
| `--raw` | Include each returned record's full saved content and evidence |
| `--source SOURCE` | Optional source filter; omitted means all sources |
| `--kind KIND` | Optional entity-kind filter |
| `--where "FIELD OP VALUE"` | Optional field condition; repeat to combine conditions |

```sh
./pipeline search radar --where "range>=100 km" --page 2
./pipeline search aircraft --source odin --limit 5
```

Filters apply before ranking and pagination. `pipeline info` lists sources, kinds,
and filter fields. Field operators are `=`, `!=`, `<`, `<=`, `>`, and `>=`.
Numeric fields require compatible units except counts; unknown or approximate
values do not satisfy strict numeric conditions. `--mode` applies to text-only
queries; image-and-text queries use hybrid fusion.

Text queries allow 1,000 characters and 256 model tokens. Query images may be up
to 20 MiB and 40 million pixels. Search uses saved source content by default;
generated OCR/descriptions can be included with `--observations`.

## Results

Commands return JSON on stdout. Search responses contain the dataset ID, query,
ranked `results`, and `page`, `limit`, `total`, and `has_more`. `total` counts the
eligible ranked records, including low-similarity candidates; it is not a count of
confirmed matches. Image-only search counts records with indexed images. Use the
same query, filters, mode, page size, and dataset to move through stable pages.
A page beyond the end returns an empty array and `has_more: false`.

Each result includes its ID, title, source URL, score, and snippet. Ranking and
source references remain available for callers that inspect them. Scores are not
probabilities. `--raw` adds `content`: the complete saved entity record, source
facts, and every evidence page with Markdown and saved HTML (or base64 for
non-UTF-8 HTML). This is captured content, not generated text.

To inspect a particular result again:

```sh
./pipeline get SOURCE:ID
```

`get --format source --evidence PAGE_ID SOURCE:ID` exports the exact captured
response bytes. This can be a source-wide response shared by several records.
Errors return a nonzero status and JSON on stderr. `pipeline search --help` shows
search usage.

## Install and build

Download the archive for your platform from the
[latest release](https://github.com/osint-builders/pipelines/releases/latest).
Extract it and run `pipeline` (`pipeline.exe` on Windows). Each executable includes
the dataset, models, search indexes, and selected image previews. It requires no
external runtime, model download, image archive, or separate data file.

CLI releases contain five platform archives and `SHA256SUMS`. Text search,
pagination, full-content results, and image lookup work from that single executable.

With a packaged dataset and the project's Python and Go build dependencies:

```sh
python tools/build_cli.py --bundle /path/to/dataset.zip --output dist/pipeline
./dist/pipeline search "airborne radar"
```

Use `dist/pipeline.exe` on Windows. `pipeline verify` checks the embedded data and
models. `SHA256SUMS` verifies release downloads; `pipeline info` describes the
embedded dataset. Quality and native-platform checks run before publication;
their build reports are kept separately from the CLI downloads.

Optional original images for this dataset remain in the
[earlier dataset-bearing release](https://github.com/osint-builders/pipelines/releases/tag/cli-292652265f9efe39e8a659b00fb25e0f0fe7020d8c82085dd0d4de8d05e37a91).
Download its `images-*.zip` parts, `image-records.json`, and `image-dataset.json`.
Extract the parts into one directory.
`image-records.json` maps source URLs and record references to SHA-256 hashes;
originals are stored at `objects/<first two hash characters>/<full hash>`.

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
