# Entity search milestones

Help researchers and educators find static entities quickly using names, descriptions,
specifications, and images, with every result linked to captured evidence.

**Progress:** 3/10 milestones verified. **Active:** M4, ready for user verification.

**Branch:** `feature/multimodal-entity-search`.

Existing foundation: 11 source adapters, shared crawl/extract/audit commands, archived
evidence, offline text search, and standalone CLI releases.

Keep this file as the current tracker. Update statuses and checkboxes as work lands.
Use `Ready for verification` when implementation and acceptance checks are complete.
Mark a milestone `Verified` only after the user verifies it, then begin the next milestone.
Record the validation artifact or commit in its status cell. Store generated reports
under ignored build/data directories. Keep the root README focused on shipped CLI
commands and the source table.

| Milestone | Depends on | Status |
| --- | --- | --- |
| M1 — Baseline and acceptance targets | Existing CLI | Verified by user; `5102380`, baseline and seed checks passed; four visual cases tracked for M3 |
| M2 — Shared media evidence and archive | M1 | Verified by user; `55d4046`, 250 tests and GitHub CI passed; offline compatibility confirmed |
| M3 — Two-source media pilot | M2 | Verified by user; `ba855a3`, 2,799 saved files, 294 tests and CI passed; two diagram cases remain unscored |
| M4 — Portable image encoder | M1, M3 | Ready for verification; `e02bbeb`, 391 tests and CI passed; all five native targets pass 23 parity probes with network restrictions verified |
| M5 — Image and combined queries | M2, M3, M4 | Planned |
| M6 — OCR and visual descriptions | M3, M5 | Planned |
| M7 — Better text and combined ranking | M1, M5, M6 | Planned |
| M8 — Entity relationships and precise filters | M1, M7 | Planned |
| M9 — Media coverage across all sources | M3, M5, M6 | Planned |
| M10 — Quality gates and compact releases | M4–M9 | Planned |

Advance in milestone order and stop for user verification between milestones.
Agents may work concurrently within the active milestone; dependencies do not authorize
starting later milestones early. Prioritize media capture from expiring sessions when
its milestone begins. Final model selection uses the pilot images from M3.

## M1 — Baseline and acceptance targets

- [x] Record current text retrieval quality, first/repeated-process latency, peak memory, and compressed release sizes.
- [x] Define representative tasks: exact designation, descriptive query, specifications, photograph, and photograph plus text.
- [x] Build seed evaluation sets covering unseen views, crops, diagrams, similar variants, and queries with no matching entity; mark missing inputs explicitly.
- [x] Separate development and evaluation images by underlying photograph, including resized and cropped duplicates.
- [x] Freeze numerical acceptance targets for retrieval quality, false matches, latency, memory, and binary size before model selection.
- [x] Define the first release's query modes, result fields, media retention, and compatibility requirements.

Complete when a repeatable baseline and explicit acceptance targets exist. Record target
hardware and distinguish measured results from proposed budgets.

Current contract: [search_acceptance.json](tests/fixtures/search_acceptance.json).
Targets are frozen following user verification of M1; image capabilities are not implemented yet.

Measured on Windows 11 / AMD Ryzen 9 5950X / 128 GiB RAM, using dataset
`572bc6071b705821d35d2b12264efdc6e7e29545dc23def6fd62b2c58db68802`
(4,337 entities, all 11 sources):

| Baseline | Result |
| --- | --- |
| Required text cases, expected entity first | 73/73 |
| Exploratory cases, expected entity first / within five | 28/43 / 37/43 |
| First observed process / median of five repeat processes | 1.987 s / 1.963 s |
| Latency p95 across 116 queries | 2.222 s |
| Peak process working set | 791.08 MiB |
| Windows executable / compressed ZIP | 184.86 MiB / 174.99 MiB |
| Compressed archives across five targets | 165.51–174.99 MiB |

These text cases use source filters. Filesystem cache state is uncontrolled; each query
loads a new process. This does not measure a reboot-cold start or a resident warm model.
Native performance on the other four targets remains unmeasured. Full measurements,
hardware, artifact hashes, and per-case outcomes are in ignored `build/m1/baseline.json`.

Repeat from the repository root with the full local CLI:

```sh
uv run --no-sync python tools/benchmark_cli.py dist/release/pipelines-windows-amd64.exe --releases dist/release --repeats 5 --output build/m1/baseline.json
```

At M1 verification, [multimodal.json](tests/fixtures/multimodal.json) contained 29 cases
and 20 captured assets (17 originals, three crops; 2.61 MiB). Ten text and 15 visual
cases had ready inputs. Four visual cases were unscored: Bofors needed an independent
gallery view; two Commons variant cases needed images; a Commons diagram needed both.
Six Commons originals returned HTTP 429 and further requests stopped. M3 restoration
and expansion results are below. The seed does not establish image-search quality or
satisfy the larger release sample minimums.

Validated photo-group separation, crop bounds, all 20 cached hashes, and all 27 media
evidence references against the full CLI. Originals and crops stay under ignored
`build/m1/media`; the fixture records exact URLs, hashes, and restoration recipes.
Future full-corpus benchmark indexes must also exclude every query photo group and its
derivatives; fixture separation alone cannot prevent later capture from introducing them.

Additional seed text measurements are in `build/m1/seed-text-baseline.json`: all nine
positive queries ranked an accepted entity within two results. The five global positives
scored 3/5 first; the four source-filtered positives scored 3/4 first. The fictional
negative returned candidates, and the current CLI has no abstention signal to score.

Validation: 196 Python tests; Ruff lint/format and full-project mypy; GitHub CI passed.
User verification authorizes M2; image-search implementation and model selection remain
in their later milestones.

## M2 — Shared media evidence and archive

- [x] Add versioned media records containing stable IDs, original URL, evidence/entity associations, captions, dimensions, MIME type, capture time, and content hash.
- [x] Let adapters describe media through one shared interface; keep website-specific discovery and supporting files in their source folders.
- [x] Implement one resumable downloader with retries, atomic temporary files, response validation, and source-scoped authentication and redirects.
- [x] Keep verified originals in a durable local archive; use temporary storage for decoding, resizing, and other intermediate work.
- [x] Deduplicate identical bytes across sources and track near-duplicate images without automatically merging entity identities.
- [x] Version processing recipes and cache derived output by media hash, model revision, and preprocessing settings.
- [x] Extend shared commands and audits to report discovered, saved, excluded, failed, and unassociated media. Keep indexing and packaging offline.

Complete when interrupted downloads resume, source associations survive deduplication,
existing text snapshots remain usable, and extraction can replay saved media offline.

Implemented in `media.py`, `media_download.py`, and `media_pipeline.py`. One optional
`MediaSource` interface describes candidates from saved responses. The shared archive
keeps originals under `DATA/media/objects`, a versioned SQLite manifest, resumable
partials under `DATA/media/tmp`, and derived artifacts keyed by original hash, recipe,
model identity/revision, and settings. Per-source/archive records retain every evidence
association. Near-duplicate fingerprints are review hints, never entity merges.

Producer commands:

```sh
pipeline-build media SOURCE --root DATA
pipeline-build media SOURCE --root DATA --download --output REPORT.json
```

The first command discovers and reports offline; `--download` enables HTTP capture.
Downloads validate JPEG/PNG/WebP originals, with 20 MiB and 40 MP limits. Interrupted
transfers resume only with matching validators; authentication failures and throttling
stop the source run. Failed records remain visible and retryable. Incomplete capture
and audits return a nonzero exit status with their JSON report.

Text snapshots remain schema 2; media uses its own schema 1. Offline extraction adds a
media sidecar to the new snapshot without changing entity or source-export bytes.
Existing adapters report `no_media_adapter` until their milestone enables discovery;
the Military Periscope/Commons implementations and live capture remain M3 work.

Validation: all 250 tests pass, including 54 media tests; Ruff lint/format, full-project
mypy, and the Python package build pass. A Windows path-prefix race found during final
validation is covered by a regression test and passed 240 concurrent cache calls.
An offline smoke check archived all 17 existing seed originals (2,210,817 bytes),
retained links to seven entities, and reused a derived preview without recomputing it.
With network access disabled, all 11 existing sources loaded: 4,337 entities and 5,090
evidence pages, with the exact M1 text content hash unchanged.
Reports: `build/m2/archive-smoke.json`, `build/m2/compatibility.json`,
`build/m2/tests.xml`. All generated data stays outside Git. User verification authorizes M3.

## M3 — Two-source media pilot

- [x] Capture Military Periscope images from structured content blocks, preserving captions and section/subject associations.
- [x] Capture Wikimedia Commons media from file records, resolving original files and available previews.
- [x] Exclude navigation graphics and unrelated illustrations; represent ambiguous or multiple depicted entities explicitly.
- [x] Publish media manifests and coverage reports for both sources through the shared pipeline.
- [x] Select representative pilot images for retrieval evaluation and inspect their entity associations.

Complete when both sources use the same media workflow and every discovered candidate
has a recorded outcome. Failed downloads remain visible and resumable.

Both adapters use the shared media interface, downloader, archive, coverage report,
and offline publication. Source-specific discovery stays in each source's folder.
Occurrences retain captions, sections, evidence/entity pairs, original/preview links,
and ambiguous associations. Saved URLs are distinct from underlying original groups.
Bounded workers share request pacing and stop on throttling; verified images survive
interruptions. Commons uses four workers with request starts spaced one second apart.

Pilot results from the existing archived pages:

| Source | Discovered URLs | Saved originals / previews | Failed | Excluded | Unassociated | Entities with media |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Military Periscope | 458 | 427 / 0 | 0 | 2 | 29 | 132/143 |
| Commons | 9,209 | 1,153 / 1,219 | 44 | 6,495 | 298 | 148/151 |

No candidates remain pending. All 280 entities with candidates have saved media;
14 entities have no candidates. The archive contains 2,799 verified image files
(2.94 GiB), covering 1,650 original groups. Commons exclusions include redundant
previews and unsupported or oversized originals. Its 44 failed originals comprise
42 unsupported formats and two invalid images; every one has a saved preview.
Failures remain retryable and keep the Commons media audit's `complete`/`ok` false.
Military Periscope's audit passes. Every discovered candidate has a recorded outcome.

Both final snapshots were published with network access disabled. Text export hashes
are unchanged. Media integrity and entity/evidence references pass validation;
capture outcomes and per-entity coverage are in `build/m3/coverage.json`.
Source audits are `build/m3/militaryperiscope-audit.json` and
`build/m3/commons-audit.json`. Local originals remain in
`C:/Users/erikz/.hai/reference-data/media/objects`; generated data stays outside Git.

The reviewed inventory is [media_pilot.json](tests/fixtures/media_pilot.json):
12 Military Periscope and nine Commons images. Source-context review records class
illustrations, renderings, site views, multiple subjects, and conflicting labels.
It does not establish exact-variant ground truth. The restored Commons seed pairs
retain their frozen M1 photograph groups and splits; shared exhibition backgrounds
and partial equipment views are recorded as evaluation limitations. Cross-source
photo-group exclusion remains required before scoring a full-corpus image index.
The seed now has 27 captured, inspected assets and 30 cases, with 28 ready inputs.
Both 1L122 variant cases are ready and a 96L6E unseen-view case is added. Two diagram
cases remain unscored: Bofors has no independent gallery image in this capture;
Dunay's comparable satellite scene may belong to the drawing's source-photo family,
while reviewed ground views lack comparable radar geometry. Existing M1 query IDs,
splits, photograph groups, captured hashes, and numerical targets are unchanged.
All 27 media evidence associations were checked against local snapshots.
Reports: `build/m3/seed-validation.json`, `build/m3/seed-pairs-review.json`, and
`build/m3/dunay-pair-review.json`.

Validation: 294 Python tests, Ruff lint/format, full-project mypy, and package build
pass. GitHub CI passed at `4f60ccd`, covering Python on Windows/Linux, Go on three
platforms, and the offline Docker smoke check. All three final seed/pilot fixture
checks pass, including the 27 locally cached image hashes.
Concurrency checks use controlled admission timing and explicit stop events, so
throttle, retry, and interrupt checks do not depend on local HTTP response speed.
The real pacing and cancellation paths remain exercised. Final CI passed at `ba855a3`.
User verification authorizes M4.

## M4 — Portable image encoder

- [x] Benchmark compact candidates, beginning with MobileCLIP2-S0, against a retrieval-quality reference model.
- [x] Prove the selected exported model works in the CLI's pure-Go runtime before committing to it.
- [x] Match Python and Go image preprocessing and embeddings using fixed image probes, including orientation, color, resize, crop, and normalization.
- [x] Compare full-precision and smaller model/vector representations against M1's quality and resource budgets.
- [x] Pin model files, hashes, dimensions, preprocessing, and output normalization in the build manifest.
- [x] Decide whether direct text-to-image search warrants shipping a paired text encoder; image-only encoding supports the initial image-query path.
- [x] Pass real-model parity and offline execution checks on all five supported native targets.

Complete when the selected model passes parity and offline execution checks on every
supported platform and architecture. Report measured size, memory, and latency.

Selected model: MobileCLIP2-S0, with float16 stored weights converted to float32
for execution. The graph returns 512 dimensions and the encoder applies L2
normalization. `src/pipelines/image_model.lock.json` pins checkpoint revision/hash,
export settings, graph hash, shape, preprocessing, and normalization. Paired visual
text encoding remains deferred under the frozen first-release contract.

One offline interface in Python and Go verifies the model before inference. Both
use the same EXIF orientation, alpha-on-white, bilinear antialiasing, center crop,
and float32 normalization recipe. Source images remain unchanged. The image-only
port matched Apple's full checkpoint on the initial probe. Construction pins
x86_64 AVX2 arithmetic to reproduce the frozen graph bytes on Windows and Linux.
Heavy export tooling runs only during construction; the Go runtime has no
native-library dependency and supports all five query targets.

The selection was frozen on development data before held-out evaluation, using
`build/m4/selection.json`. S0 float32, S0 half-stored, and the larger MobileCLIP2-B
reference all rank 6/6 development positives first against nine gallery entities.
On held-out inputs, selected S0 ranks 8/9 positives first and within five, with
6/7 underlying photograph groups passing all their cases. The stowed Lanza truck
is missed; B also misses it globally, but retrieves it within five with a source
filter. Half precision for weights or stored gallery vectors causes no ranking
regression. One sky negative has a score only; abstention is M5 work. Two combined
queries and two diagrams without independent references stay unscored.

This small seed cannot establish release quality: six development positives have
no negative controls, nine held-out positives share seven photograph groups, and
the release sample minimums remain unmet. Frozen acceptance targets are unchanged.
Reports: `build/m4/development-encoders.json`, `build/m4/evaluation-encoders.json`.

Windows encoder measurements on the M1 reference machine:

| Representation | Model / zipped model | First process | Repeated process p95 (20 launches) | Peak OS working set |
| --- | --- | ---: | ---: | ---: |
| Float32 | 43.4 / 40.3 MiB | 0.875 s | 0.930 s | 213.3 MiB |
| Float16 storage, float32 execution | 21.8 / 20.2 MiB | 0.907 s | 0.853 s | 180.3 MiB |

These include image decoding and model loading, but exclude the text bundle and
entity search. First observed launch is not a cache-cleared cold start. The model,
vector, preview, and runtime costs must still fit together at M10. A half-stored
10,000-view gallery requires 9.77 MiB for 512-dimensional vectors before metadata.
Measurements and artifact hashes are in `build/m4/performance.json`.

Validation: 391 Python tests, Ruff lint/format, full-project mypy, package build,
Go tests/vet, and the existing offline Docker smoke check pass. All five native
targets pass 23/23 real-model parity probes: Windows amd64, Linux amd64/arm64,
and macOS amd64/arm64. Twenty procedural images cover orientation, color, alpha,
resizing/cropping, and normalization; three tensors isolate graph execution.
Linux uses isolated network namespaces, macOS uses sandboxing, and Windows blocks
outbound traffic from the probe executable. Windows and macOS confirm OS-denied
connections before and after inference; timeouts do not count as proof.

All ten real pilot images also pass on Windows, with minimum Python/Go cosine
0.999468 and maximum normalized difference 0.006125; the gates are 0.999 and 0.01.
Reports: `build/m4/pilot-embedding-parity.json`, `build/m4/native-ci-summary.json`,
and per-target reports under `build/m4/native-ci`. At `e02bbeb`, both
[regular CI](https://github.com/osint-builders/pipelines/actions/runs/35627989559)
and [native encoder checks](https://github.com/osint-builders/pipelines/actions/runs/35627989633)
pass. Model selection, held-out labels, and frozen acceptance targets are unchanged.
The root README and existing text CLI behavior remain unchanged. M5 waits for
user verification of M4.

## M5 — Image and combined queries

- [ ] Store image vectors separately from the existing text vectors, with model identity and media/evidence/entity references.
- [ ] Implement image queries and image-plus-text queries through the existing `search` command and filters.
- [ ] Combine rankings from compatible retrieval channels; never directly add embeddings from unrelated models.
- [ ] Return one result per entity with matching media IDs, source pages, and the contributing retrieval channels.
- [ ] Keep diverse image views and prevent duplicate or numerous images from dominating entity rankings.
- [ ] Add media inspection/export and embed compact previews within the distribution budget, keeping originals in the local archive.
- [ ] Extend bundle validation and `verify` to check image vectors, media references, and cross-runtime probes.

Complete when a local photograph retrieves the expected pilot entities offline and the
researcher can inspect the exact supporting image. Existing text commands must still pass.

Proposed query interface, pending implementation:

```sh
pipelines search --image photograph.jpg
pipelines search --image photograph.jpg "truck-mounted"
pipelines search --image photograph.jpg --kind radar --limit 5
```

## M6 — OCR and visual descriptions

- [ ] Extract text from markings, labels, and diagrams while retaining image regions and extraction confidence where available.
- [ ] Generate concise visual descriptions during dataset construction, with the model and processing recipe recorded.
- [ ] Store generated observations separately from source statements and structured specifications.
- [ ] Index OCR and descriptions as evidence-linked text, with their origin visible in search results.
- [ ] Reuse unchanged analysis and verify that OCR/descriptions improve held-out retrieval without increasing false identification.

Complete when visual details become searchable through text and every derived statement
can be traced to an image. Heavy analysis models run during construction, not CLI queries.

## M7 — Better text and combined ranking

- [ ] Add full-text lexical ranking alongside semantic search, preserving exact designation and alias matches.
- [ ] Index original names, abbreviations, reviewed alternate names, captions, and OCR without converting incidental mentions into aliases.
- [ ] Tune rank fusion against the evaluation set and keep text, image, and combined query behavior explicit.
- [ ] Test partial names, spelling variations, specification phrases, and queries with no supported match.
- [ ] Expose useful match reasons and treat similarity scores as ranking signals rather than identity probabilities.

Complete when the frozen text baseline has no unacceptable regression and the combined
retrieval targets pass. Keep exact vector search until measurements justify another index.

## M8 — Entity relationships and precise filters

- [ ] Link equivalent records across sources while preserving every source-qualified ID and its evidence.
- [ ] Represent family, variant, component, and related-system relationships distinctly from equivalence.
- [ ] Add evidence-backed manufacturer, country, date, and specification fields, preserving original values, units, qualifiers, and unknowns.
- [ ] Add deterministic filters and numeric comparisons over normalized fields; distinguish publication/capture dates from dates describing the entity.
- [ ] Provide machine-readable relationship and comparison output suitable for research scripts and teaching material.
- [ ] Validate ambiguous names and visually similar variants so incorrect merges do not become permanent identities.

Complete when researchers can follow a subject across sources, distinguish variants,
and reproduce filtered results without losing the original records.

## M9 — Media coverage across all sources

- [ ] Complete the rollout table below using the shared interfaces; keep adapters limited to website-specific behavior.
- [ ] Audit original image resolution, captions, entity associations, duplicates, and failed captures for each source.
- [ ] Build image vectors and derived text for applicable media and publish per-source coverage metrics.
- [ ] Verify unchanged media reuse cached analysis and vector output on repeat builds.
- [ ] Require an explicit outcome for sources or records with no applicable images; avoid substituting logos or unrelated pictures.

Complete when every source has an audited media capability and the full dataset can be
rebuilt from local captures. Each source must account for outstanding failures.

| Source | Status | Completion evidence |
| --- | --- | --- |
| militaryperiscope | Media pilot captured; M9 processing pending | M3: 427 originals; 132 entities with media |
| commons | Media pilot captured; M9 processing pending | M3: 2,372 originals/previews; 148 entities with media; 44 failed originals have previews |
| radartutorial | Pending | — |
| deagel | Pending | — |
| virtualglobetrotting | Pending | — |
| russianforces | Pending | — |
| wikipedia | Pending | — |
| armyrecognition | Pending | — |
| fandom | Pending | — |
| climateviewer | Pending | — |
| cambridgepixel | Pending | — |

## M10 — Quality gates and compact releases

- [ ] Run the frozen evaluation suite for text, image, combined, OCR, filters, and entity relationships; report results by task and source.
- [ ] Verify top-result accuracy, recall within the first five results, confusable variants, and no-match behavior meet M1's targets.
- [ ] Measure cold/warm latency, peak memory, and compressed executable size on all supported targets.
- [ ] Build and test one standalone executable per platform with required models, indices, evidence, and selected previews embedded.
- [ ] Validate deterministic dataset identities, cached rebuilds, checksums, source exports, and operation without network access.
- [ ] Run lint, types, unit/integration tests, Python/Go parity, and native release acceptance checks.
- [ ] Update the root README with only shipped CLI options/API behavior and the source table.
- [ ] Publish the verified binaries and checksums through the existing manual release process.

Complete when the released CLI satisfies the agreed quality and distribution budgets
and its results identify the supporting source and media evidence.
