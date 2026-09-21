# Military Periscope

The `militaryperiscope` adapter captures trial catalog subjects and their related
sections, including weapons, armed forces, companies, and militant organizations.

## Endpoints

The `_next/data/...json` endpoints contain cropped previews. Full content comes from
the authenticated API at `https://militaryperiscope.com`:

- `/wt/api/nextjs/v1/trial_content/`: catalog tree.
- `/wt/api/nextjs/v1/page_by_path/?html_path=...`: page content.
- `/trial-access/`: entry page.

Discovery follows `related_url` within each subject. Native IDs collapse repeated
memberships while retaining categories. Cross-references and media remain links.
Cropped sections marked `restricted: true` are archived but excluded from the index.

## Crawl and resume

Set `MILITARYPERISCOPE_COOKIE_FILE` to a file containing the Cookie header value.
`MILITARYPERISCOPE_COOKIE` can supply the header directly and takes precedence.

```powershell
$env:MILITARYPERISCOPE_COOKIE_FILE = (Resolve-Path .env.militaryperiscope).Path
uv run --no-sync pipeline-build crawl militaryperiscope --root ../pipeline-data
uv run --no-sync pipeline-build status militaryperiscope --root ../pipeline-data
```

Repeat `crawl` to resume an interrupted archive. Requests require
`Accept: application/json`; Django otherwise returns its HTML API browser.
Expired access or HTTP 401/403 stops the crawl. Missing subjects or sections prevent
snapshot publication. A completed refresh starts a new archive.

## Extraction and checks

Entity IDs use native subject IDs, such as `militaryperiscope:317977`. Country chapters
and weapon sections merge under their parent subject. Content blocks render to HTML
and Markdown while preserving text, tables, dates, units, and archive flags.
`get --format source` exports the original JSON bytes.

```sh
uv run --no-sync pipeline-build extract militaryperiscope ARCHIVE_ID --root ../pipeline-data
uv run --no-sync python tools/audit_militaryperiscope.py --root ../pipeline-data --binary dist/pipelines --output build/coverage.json
pipelines search --source militaryperiscope "AU-21 Puma"
pipelines get --format markdown militaryperiscope:317977
```

Use `dist/pipelines.exe` for the audit on Windows. The audit checks catalog coverage,
content retention, and exported bytes against the archive and snapshot.
