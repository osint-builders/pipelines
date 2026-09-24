# pipelines

Search radar, military equipment, site, and signal references offline. One
executable contains the dataset, search models, indexes, and image previews.
Queries stay on your machine.

```sh
pipeline search "airborne radar"
pipeline search "airborne radar" --mode vector
pipeline search --image radar.jpg
```

Search covers every source by default. `hybrid` combines semantic similarity with
keyword and name matching; `vector` uses semantic cosine similarity alone. An image
query searches the indexed image gallery. Adding text combines both rankings.
Image results are candidates for review; similarity alone does not establish identity.

## Contents

- [Install](#install)
- [Search options](#search-options)
- [Analyst examples](#analyst-examples)
- [Results and evidence](#results-and-evidence)
- [Build from source](#build-from-source)
- [Sources and counts](#sources-and-counts)

## Install

Download your platform's archive and `SHA256SUMS` from the
[latest release](https://github.com/osint-builders/pipelines/releases/latest).

| Environment | Archive |
| --- | --- |
| Windows, Intel/AMD 64-bit | `pipelines-windows-amd64.zip` |
| macOS, Apple Silicon | `pipelines-darwin-arm64.tar.xz` |
| macOS, Intel | `pipelines-darwin-amd64.tar.xz` |
| Linux or WSL, Intel/AMD 64-bit | `pipelines-linux-amd64.tar.xz` |
| Linux or WSL, ARM64 | `pipelines-linux-arm64.tar.xz` |

Extract the archive, then follow the instructions for your shell from the extracted
directory. These install `pipeline` for your user account, so you can run it from
any directory without `./`. No administrator access, Python, Go, or separate model
or dataset download is needed.

### Windows PowerShell

Copy the executable to a permanent location and add that directory to your user PATH:

```powershell
$installDir = Join-Path $env:LOCALAPPDATA 'Programs\pipeline'
New-Item -ItemType Directory -Force -Path $installDir | Out-Null
Copy-Item -LiteralPath .\pipeline.exe -Destination $installDir -Force
$userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if ($installDir -notin ($userPath -split ';')) {
    [Environment]::SetEnvironmentVariable('Path', "$installDir;$userPath", 'User')
}
$env:Path = "$installDir;$env:Path"
pipeline --version
```

The PATH change applies to this shell and new terminal sessions. To update,
replace the installed executable with one from a newer release.

### macOS with zsh

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 pipeline "$HOME/.local/bin/pipeline"
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.zshrc" 2>/dev/null ||
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.zshrc"
export PATH="$HOME/.local/bin:$PATH"
pipeline --version
```

If macOS blocks the downloaded executable, review it in System Settings under
Privacy & Security before allowing it to run.

### Linux or WSL with bash

Use the Linux archive inside WSL. Install it with:

```sh
mkdir -p "$HOME/.local/bin"
install -m 755 pipeline "$HOME/.local/bin/pipeline"
grep -qxF 'export PATH="$HOME/.local/bin:$PATH"' "$HOME/.bashrc" 2>/dev/null ||
  printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$HOME/.bashrc"
export PATH="$HOME/.local/bin:$PATH"
pipeline --version
```

For another shell, add `$HOME/.local/bin` to its PATH configuration. On macOS and
Linux, rerun the `install` command from a newer extracted release to update.

### Verify the download

Before extracting, compare the archive's SHA-256 with its entry in `SHA256SUMS`.
For example:

```powershell
Get-FileHash .\pipelines-windows-amd64.zip -Algorithm SHA256
```

```sh
# Linux
sha256sum pipelines-linux-amd64.tar.xz
# macOS
shasum -a 256 pipelines-darwin-arm64.tar.xz
```

After installation, `pipeline verify` checks the embedded dataset and models.
`pipeline info` shows the dataset ID, coverage, and available filter fields.

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
| `--observations` | Include generated image descriptions and OCR in text search |

```sh
pipeline search radar --where "range>=100 km" --page 2
pipeline search aircraft --source odin --limit 5
```

Filters apply before ranking and pagination. `pipeline info` lists sources, kinds,
and filter fields. Field operators are `=`, `!=`, `<`, `<=`, `>`, and `>=`.
Numeric fields require compatible units except counts; unknown or approximate
values do not satisfy strict numeric conditions. `--mode` applies to text-only
queries; image-and-text queries use hybrid fusion.

Text queries allow 1,000 characters and 256 model tokens. Query images may be up
to 20 MiB and 40 million pixels. Search uses saved source content by default;
generated OCR/descriptions can be included with `--observations`.

## Analyst examples

These examples search captured references. They do not establish current equipment
deployments, site activity, or whether a transmitter is on air. Check the saved
evidence and its capture date before using a result in an assessment.

### Find equipment from a capability description

Start with the function you need to investigate when you do not know a designation:

```sh
pipeline search "mobile radar for detecting low flying aircraft" --mode vector --limit 10
pipeline search "coastal surveillance radar" --source cambridgepixel --limit 10
pipeline search "counter battery radar" --source odin --raw --limit 3
```

Vector search finds semantically similar passages. Hybrid search, the default,
also rewards names and keywords. Compare both when a description uses different
terminology from the source. `--raw` includes the full saved content for the
returned records, so you can check operating role, platform, and supporting claims.

### Research a designation across sources

Search all sources first, then narrow the review to individual references:

```sh
pipeline search "S-300" --limit 20
pipeline search "S-300" --source deagel --raw --limit 5
pipeline search "S-300" --source odin --raw --limit 5
```

Records retain their source-specific IDs. Several results may describe the same
family, a variant, or a component. Compare the evidence before treating them as
the same system; repeated claims across websites may share an original source.

### Shortlist systems by reported specifications

Combine a description with numeric conditions to find candidates for a capability
comparison:

```sh
pipeline search "air surveillance radar" --where "range>=100 km" --limit 20
pipeline search "air surveillance radar" --where "detection_range>=100 km" --raw --limit 5
pipeline search "air surveillance radar" --where "range>=100 km" --where "range<=500 km"
```

Conditions apply before ranking. Each must have supporting indexed claims, so a
record with missing data can be excluded even if it is operationally relevant.
`range` and `detection_range` are separate fields. Conditions can be supported by
different claims in the same record. Read the source's definition, target
assumptions, and variant before comparing numbers.

### Review radar and air-defense site references

Use site-oriented sources to build a list for further geographic research:

```sh
pipeline search "early warning radar" --source virtualglobetrotting --raw --limit 5
pipeline search "surface to air missile site" --source climateviewer --page 1 --limit 20
pipeline search "surface to air missile site" --source climateviewer --page 2 --limit 20
```

These are ranked reference searches. They do not perform a radius search or
confirm that a site remains active. Use coordinates and dates in the saved record,
where present, to decide what needs checking against newer material.

### Compare signal references

Search signal names, modulation descriptions, or likely roles in SigIDWiki:

```sh
pipeline search "over the horizon radar" --source sigidwiki --raw --limit 5
pipeline search "frequency shift keying telemetry" --source sigidwiki --mode vector
pipeline search --image waterfall.png --source sigidwiki --limit 10
```

The image query compares a JPEG or PNG waterfall screenshot with indexed images.
It does not decode IQ samples or audio. Frequency spans describe reported ranges;
they do not identify an individual occupied channel. Compare the written signal
description as well as the waterfall pattern.

### Investigate an equipment photograph

Start with the photograph, then add context you can support independently:

```sh
pipeline search --image radar.jpg --limit 10
pipeline search "coastal surveillance" --image radar.jpg --raw --limit 5
pipeline search "rectangular antenna on a truck" --observations --limit 10
```

Image-only search covers records with indexed imagery. Adding text combines image
and text rankings. `--observations` searches generated descriptions and OCR as well
as source content; it can help find visible features, but those generated passages
need checking against the image and source. A high score does not verify a model
or variant.

### Save a paginated evidence review

Keep the query, filters, and page size fixed while reviewing a larger candidate pool:

```sh
pipeline search "airborne early warning" --page 1 --limit 20 --raw > review-page-1.json
pipeline search "airborne early warning" --page 2 --limit 20 --raw > review-page-2.json
```

Continue while the response has `has_more: true`. Keep the dataset ID with your
notes so another analyst can reproduce the search against the same release.
To inspect one result later, replace `SOURCE:ID` below with its returned `id`:

```sh
pipeline get SOURCE:ID
pipeline get SOURCE:ID --format markdown > record.md
pipeline get SOURCE:ID --format source --evidence PAGE_ID > evidence.bin
```

For the last command, use an evidence ID from the record. `source` writes the exact
captured response bytes, which may be HTML or JSON and may cover several records.
Use a shell that preserves native binary output when redirecting this format,
such as bash, zsh, or
[PowerShell 7.4 and later](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_redirection#redirecting-output-from-native-commands).

## Results and evidence

Search and metadata commands return JSON on stdout. Search responses contain the
dataset ID, query, ranked `results`, and `page`, `limit`, `total`, and `has_more`. `total` counts the
eligible ranked records, including low-similarity candidates; it is not a count of
confirmed matches. Image-only search counts records with indexed images. Use the
same query, filters, mode, page size, and dataset to move through stable pages.
A page beyond the end returns an empty array and `has_more: false`.

Each result includes its ID, title, source URL, score, and snippet. Ranking and
source references remain available for callers that inspect them. Scores are not
probabilities. `--raw` adds `content`: the complete saved entity record, source
facts, and every evidence page with Markdown and saved HTML (or base64 for
non-UTF-8 HTML). This is captured content, not generated text.

Errors return a nonzero status and JSON on stderr. `pipeline search --help` shows
search usage.

## Build from source

The Python package builds datasets; the Go project builds the offline CLI.
Install Python 3.13, uv, and the Go version declared in `cli/go.mod`, then run
these commands from the repository root with a verified packaged dataset:

```sh
uv sync --frozen --extra build --extra image
uv run python tools/build_cli.py --bundle /path/to/dataset.zip --output dist/pipeline
./dist/pipeline search "airborne radar"
```

Use `--output dist/pipeline.exe` on Windows. Install the resulting executable with
the PATH instructions above. Release publication requires dataset-bound quality
checks and native checks on every supported platform. Public CLI downloads contain
the five platform archives and `SHA256SUMS`.

## Sources and counts

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
| [sigidwiki](https://www.sigidwiki.com/wiki/Database) | 598 | 598 | 580 | Signal table records and waterfall images; placeholder images are excluded from image search |
| **Total** | **9,061** | **10,193** | **22,462** | |
