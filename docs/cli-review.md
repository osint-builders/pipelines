# CLI interface review

Reviewed September 23, 2026. The main interface is now `pipeline search`, with
pagination, optional full content, image lookup, and optional filters. `get` and
`info` support that workflow. Existing specialist commands remain callable for
integrations and audits; they are documented in the separate reference.

## Findings and changes

| Finding | Impact | Resolution |
| --- | --- | --- |
| Top-level help advertised 13 command names and internal research concepts | A basic search required learning the archive's implementation | Main help presents search, get, and info; search has its own focused help |
| Search accepted a result limit but no page | Records below rank 100 were inaccessible even though ranking already scanned the complete pool | Rank the eligible pool, then select a page; return page, limit, total, and has_more |
| Every option had to precede the query | Natural commands such as `search radar --page 2` failed | Parse options in either position, including `--option=value` and `--` for literal dash-prefixed words |
| Multiple query words required shell quotes | Common terminal input was rejected as extra arguments | Join positional search words; quoted queries continue to work |
| Full content required copying IDs and issuing another command | Looking through several complete results took several calls | `search --raw` includes the complete saved entity and evidence for the selected page |
| The public executable name was plural | It differed from the requested `./pipeline search` interface | Help uses `pipeline`; newly built release archives contain `pipeline` or `pipeline.exe` |
| Help did not give an easy inventory of entity kinds | Optional filters required guessing names | `info` now reports sorted kinds alongside the existing source and field catalogs |
| Paging could accidentally change calibration or cause expensive exports | Later pages could produce different query decisions or load unnecessary evidence | Keep the global leaders for calibration; resolve provenance only for the selected page and any required leaders |

## Search behavior

There is no source restriction by default. The current dataset has 9,061 entities
across 13 sources. Text retrieval scores each entity's best source-text chunk and
returns one result per entity. The text index has 55,128 chunks. Each captured
evidence page is split into overlapping model-sized chunks by the builder; there
is no query-time truncation to the first 100 entities.

`--mode vector` is semantic search: exact cosine similarity in the embedded text
model's vector space. `--mode hybrid` adds keyword/name matching, source captions,
and supported numeric clauses. Hybrid remains the default to preserve useful
name lookup and the measured retrieval baseline. These are ranking policies, not
separate databases. Source captions contribute to hybrid keyword ranking; they
are not a separate caption-vector index. Generated OCR and descriptions remain
explicitly opt-in with `--observations` so generated statements do not silently
become source evidence.

`--image` uses the embedded image model to search the saved, indexed gallery.
Image-only search can return the 8,171 entities with indexed image associations.
Text plus image combines text and image rankings, so it can also return entities
with no indexed photograph. A saved image URL is not necessarily an indexed image:
the distribution retains originals outside the selected gallery. Neither image
similarity nor a high text score establishes identity.

Filters apply before final ranking and paging. `--source` and `--kind` are exact
matches. `--where` uses the existing typed, evidence-backed field predicates;
repeated predicates require every condition. An absent filter value produces an
empty pool. Invalid field names, operators, or incompatible numeric units are
errors. `info` provides the available source, kind, and field names.

## Pagination contract

- `--page` is one-based. `--limit` is the page size, from 1 to 100.
- `total` is the full eligible ranked pool, not a relevance threshold or a count
  of verified matches. Image-only totals count entities with indexed images.
- Results keep their existing deterministic score ordering and entity-ID tie
  breaker. Paging does not rerank a subset or change scores.
- A page beyond the end has `results: []` and `has_more: false`; very large page
  numbers cannot overflow the offset calculation.
- The same query, image, filters, mode, page size, and dataset ID produce stable
  page boundaries. Changing any of them starts a different search.
- Optional calibration uses the query's global leaders, including on later or
  empty pages. It does not classify the first item on each page as a new winner.

Numbered pages fit the immutable, local dataset. A cursor or server-side session
would add state without avoiding inference: each CLI invocation loads its models
and ranks the eligible pool. Pagination bounds returned content; it does not make
each new invocation an incremental search. No service, cache daemon, or network
dependency was added.

## Content and output

JSON remains the output contract. Existing scores, provenance, and ranking
metadata remain available to integrations. No second output-format selector is
needed for ordinary search. Errors remain JSON on stderr with a nonzero exit
status; help is readable text and works without loading the dataset or models.

`--raw` adds a result's complete saved record under `content`, including source
facts and all associated evidence pages with Markdown and captured or rendered
HTML. Non-UTF-8 HTML retains its existing base64 representation. Generated OCR
and descriptions are not substituted for original evidence. Only records on the
requested page are exported.

An entity record and an original HTTP response are different things. Some sources
publish many records in one response. The existing `get --format source` export
continues to return the exact captured response bytes; this can include records
beyond the selected entity. That specialist operation belongs in the reference,
rather than becoming another flag on ordinary search.

## Compatibility and distribution

The existing `similar`, `list`, `media`, `observations`, `facts`, `relationships`,
`compare`, `verify`, `version`, and `notices` commands are retained. Their output
and limits remain compatible; ordinary search gains additive pagination metadata
and optional content. `--category` and `--observations` remain available for
existing callers. Both executable basenames work when the binary is renamed;
behavior does not depend on its filename.

Release artifact names still use the repository's `pipelines-PLATFORM` prefix;
the executable inside new archives is singular. Previously published immutable
archives are not modified.

The distribution follow-up fixes the dataset-only release identity: new tags
include the dataset identity and CLI commit, allowing code-only updates. Releases
publish exactly five platform archives and checksums. Original-image archives and
validation evidence remain separate from these downloads. Dataset verification,
quality gates, and native checks are still required before publication.

## Verification

Regression tests exercise the public search methods and CLI argument/output
handling: more than 100 results, stable tied ranks, filtering before pagination,
empty and extreme pages, image-only and combined search, generated text, global
calibration leaders, and opt-in saved-content export. Archive tests check the
actual extracted executable names. The standalone CLI is also checked against
the complete captured dataset and the existing retrieval suite.

Validation on Windows with the 9,061-entity dataset:

- All Go tests and `go vet` passed.
- All 1,259 Python tests passed; Ruff and mypy checks passed.
- 31 standalone commands verified paging, full-content export, filters, empty
  and extreme pages, image lookup, combined queries, and generated-text search.
  First-page results and scores matched the previously published executable.
- The 116-case retrieval suite passed all 73 required cases. Recall at five
  remained 110/116 and mean reciprocal rank remained 0.9018162871611147.
- Embedded verification passed all four text, three image, and three
  generated-text probes.
- Native acceptance passed, including sampled records from all 13 sources,
  exact evidence exports, structured queries, and image search.

The dataset and model files were not regenerated for these changes. Native
executable checks used Windows; the Go tests also run on Linux and macOS in CI.

Implementation is concentrated in `cli/cmd/pipelines/arguments.go` (the user
interface), `search.go` (shared response/content policy), and the dataset's paged
search methods. The encoders, source adapters, ranking algorithms, and dataset
format are unchanged.
