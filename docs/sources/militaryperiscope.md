# Military Periscope

The `militaryperiscope` source captures the complete **accessible trial catalog**.
The September 20, 2026 capture contains 143 subjects: 125 weapons,
9 armed-forces profiles, 2 defense companies, and 7 historical militant organizations.
There are 263 original responses (19,110,730 bytes) and 164 full evidence pages.

## Access and discovery

The supplied `_next/data/shy-husky-74/...json` endpoints contain static, cropped
previews, even when a session cookie is supplied. The adapter uses the same
authenticated endpoints as the website:

- `/wt/api/nextjs/v1/trial_content/` supplies all trial tabs as a nested tree.
- `/wt/api/nextjs/v1/page_by_path/?html_path=...` supplies full page content.
- `/trial-access/` retains the entry page.

The catalog exposes 239 unique nodes, including collection pages. Six repeated
memberships collapse by native ID/path while retaining all categories. The militant
section is returned by the catalog API but hidden by the trial page's three-tab UI;
it is included because it was explicitly requested. Collection pages may list paid
records; those listings do not expand the accessible trial scope. Detail discovery
follows `related_url` within the same subject, including country chapters and
additional weapon sections. Narrative cross-references and media remain links.

Serbia's **Force Structures** chapter returns `restricted: true` and
`restriction_type: login`. Its response is archived and reported by the coverage
audit, but its cropped text is excluded from the index. The other 164 detail pages
return full content. All 143 trial catalog subjects have full primary pages.

## Configuration and resuming

Set `MILITARYPERISCOPE_COOKIE_FILE` to a file containing the Cookie header value.
`MILITARYPERISCOPE_COOKIE` can supply the header directly and takes precedence.
The initial local run uses `.env.militaryperiscope`.

```powershell
$env:MILITARYPERISCOPE_COOKIE_FILE = (Resolve-Path .env.militaryperiscope).Path
uv run --no-sync pipeline-build crawl militaryperiscope --root C:/Users/erikz/.hai/reference-data
uv run --no-sync pipeline-build status militaryperiscope --root C:/Users/erikz/.hai/reference-data
```

Repeat the crawl command to resume an interrupted archive. Cookies are loaded for
crawl requests.
`Accept: application/json` is necessary because Django otherwise returns its HTML API
browser to Scrapy.
Expired trial access or HTTP 401/403 stops the crawl; missing subjects/sections prevent
publication. A completed refresh starts a new archive as with other sources.

## Offline representation

Entity IDs use native subject IDs from the trial tree, such as
`militaryperiscope:317977` (AU-21 Puma). Child page IDs are separate metadata fields;
country chapters and weapon sections merge under their parent subject. Families and
variants remain together. Aliases are not inferred from components or related items.
Country, company, and organization reports use the existing `item` kind and retain
their source categories. Reviewed optical/sonar exceptions avoid classifying every
member of the publisher's radar categories as radar.

Typed content blocks render to HTML and Markdown with full prose, headings, tables,
preformatted specifications, variants, chronology, units, uncertainty, source dates,
and archive flags. Original JSON remains byte-exact under `get --format
source`. Search text omits image filenames and capture metadata. Unknown block
layouts, conflicting identities, mismatched paths, or missing content fail extraction.

## Verification

```powershell
uv run --no-sync pipeline-build extract militaryperiscope 20260920T165243Z-ad5e5522 --root C:/Users/erikz/.hai/reference-data
uv run --no-sync python tools/audit_militaryperiscope.py --root C:/Users/erikz/.hai/reference-data --binary dist/pipelines-windows-amd64.exe --output build/militaryperiscope-evaluation/coverage-and-exports.json
```

The audit compares every trial subject and discovered section to the published
snapshot, checks 10,236 source content fragments, and optionally
compares every CLI JSON-source/Markdown/HTML export byte for byte. Reports and run
logs are under `build/militaryperiscope-evaluation/`. Regression tests use synthetic
fixtures.

The source adds 4,107 vectors. All 18 CLI retrieval checks pass on Windows and Linux:
10 exact names rank first; all 8 semantic queries reach the top five (5 rank first).
Aries and Saab rank third for their semantic descriptions; the Tu-95 ranks second.
These are small diagnostics, not a general accuracy estimate. Windows exports for
all 164 pages match the archive/snapshot byte for byte. Linux amd64 passes acceptance
with networking disabled and a read-only filesystem; Linux arm64 passes verification
under emulation. macOS binaries are cross-compiled; native acceptance remains pending.

The combined local release contains 11 sources, 4,337 entities, 5,090 evidence pages,
and 19,379 vectors. Dataset ID:
`572bc6071b705821d35d2b12264efdc6e7e29545dc23def6fd62b2c58db68802`.
Five platform binaries, `dataset-manifest.json`, and `SHA256SUMS` are in `dist/`.

```powershell
dist/pipelines-windows-amd64.exe search --source militaryperiscope "AU-21 Puma"
dist/pipelines-windows-amd64.exe get --format markdown militaryperiscope:317977
dist/pipelines-windows-amd64.exe get militaryperiscope:315890
```
