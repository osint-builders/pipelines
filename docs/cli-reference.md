# CLI reference and compatibility

The primary interface is `pipeline search`. This reference covers additional
inspection and export commands kept for existing integrations. The older
`pipelines` executable name can still be used by renaming the binary.

## CLI

Options can appear before or after query words and IDs. Use source-qualified IDs
returned by a search.

Search also accepts `--page N` and `--raw`; `--limit` is the page size for search.
The legacy `list` and `similar` commands retain their original result limits.
`search --raw` includes complete saved records directly in the selected page.

| Command | Output |
| --- | --- |
| `pipeline search [options] "query"` | Ranked text search results |
| `pipeline search --image PATH [options] ["query"]` | Image or combined image-and-text suggestions |
| `pipeline media [--id MEDIA_ID] [--output PATH] SOURCE:ID` | Image metadata or one embedded JPEG preview |
| `pipeline observations [--id OBSERVATION_ID] SOURCE:ID` | Generated text, image references, and processing recipes |
| `pipeline similar [options] SOURCE:ID` | Semantically similar entities |
| `pipeline list [options]` | Entities matching filters, sorted by ID |
| `pipeline get [options] SOURCE:ID` | Entity, original source facts, and archived evidence |
| `pipeline facts SOURCE:ID` | Indexed claims, parsed values, and source evidence |
| `pipeline relationships [--type TYPE] SOURCE:ID` | Evidence-backed relationships |
| `pipeline compare SOURCE:ID OTHER:ID [...]` | Field comparisons for 2–20 distinct entities |
| `pipeline info`, `pipeline version`, `pipeline verify` | Dataset details, executable version, or integrity/model checks |
| `pipeline --help` | Help; also available with `pipeline COMMAND --help` |

| Option | Commands | Meaning |
| --- | --- | --- |
| `--mode hybrid\|vector` | `search` | Names, source captions, verified numeric clauses, and semantic ranking (default `hybrid`), or semantic ranking only |
| `--observations` | `search` | Include generated OCR/descriptions; requires a text query and can be combined with an image |
| `--image PATH` | `search` | Local JPEG/PNG, up to 20 MiB and 40 million pixels; incompatible with `--mode` |
| `--id MEDIA_ID`, `--output PATH` | `media` | Select a record; `--output` requires `--id` and writes its preview to a new file |
| `--id OBSERVATION_ID` | `observations` | Inspect one generated record and its recipe |
| `--limit N` | `search`, `similar`, `list` | Page size for search; result limit for similar/list. 1–100, default 10 |
| `--page N`, `--raw` | `search` | One-based page number; include the complete saved record and evidence |
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
pipeline search --source odin --limit 5 "HIMARS"
pipeline search --image radar.jpg --source cambridgepixel --limit 5
pipeline search "range: 550 km; weight: 54 t"
pipeline search --observations "yellow helicopter with landing skids"
pipeline list --source odin --where "origin_country=United States" --limit 5
pipeline get --format markdown odin:0a50e596d1ad19fa32b0521d94bb31a8
pipeline facts odin:0a50e596d1ad19fa32b0521d94bb31a8
pipeline search --source sigidwiki "Automatic Identification System"
pipeline list --kind signal --where "modulation=FMCW" --where "bandwidth<=50 kHz"
pipeline search --source sigidwiki --image waterfall.png
```

## API

Run the executable as a subprocess and parse JSON on stdout. Errors return a nonzero
exit code and `{"error":"message"}` on stderr. Help and non-JSON `get` formats return
text or captured bytes. Search responses include `dataset_id` so callers can identify
the data used.

`search` returns `query`, `query_type`, `mode`, `match_status`, `results`,
`page`, `limit`, `total`, and `has_more`. With `--raw`, each result includes
`content`, the complete saved entity record and evidence.
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
