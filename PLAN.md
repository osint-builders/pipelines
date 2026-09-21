# Entity search milestones

Help researchers and educators find static entities quickly using names, descriptions,
specifications, and images, with every result linked to captured evidence.

**Progress:** 9/10 milestones verified. **Active:** M10 in progress.

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
| M4 — Portable image encoder | M1, M3 | Verified by user; `e02bbeb`, 391 tests and CI passed; all five native targets pass 23 parity probes with network restrictions verified |
| M5 — Image and combined queries | M2, M3, M4 | Verified by user; `9bb1574`, 426 tests and CI pass; full pilot CLI, offline acceptance, held-out rankings, and Windows resource checks complete |
| M6 — OCR and visual descriptions | M3, M5 | Verified by user; `3bb8c29`, 542 tests and CI pass; 1,694 observations, cache reuse, held-out ranking and offline CLI checks complete; opt-in text resource gaps tracked in M10 |
| M7 — Better text and combined ranking | M1, M5, M6 | Verified by user; `0d20f4a`, CI and offline acceptance pass; 116 baseline ranks preserved, new text 18/18 within five; latency and calibration gaps tracked in M10 |
| M8 — Entity relationships and precise filters | M1, M7 | Verified by user; `beafc67b`, CI and offline acceptance pass; 63 evidence checks, 116 unchanged baseline ranks; resource gaps tracked in M10 |
| M9 — Media coverage across all sources | M3, M5, M6 | Verified by user; `62d438c`, 822 tests and CI pass; all 11 sources audited, 9,696 saved media records, deterministic offline rebuild and Windows/Linux CLI acceptance |
| M10 — Quality gates and compact releases | M4–M9 | In progress; runtime profiling, compact gallery allocation, evaluation coverage, and release identity/validation |

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
The root README and existing text CLI behavior remain unchanged. User verification
authorizes M5.

## M5 — Image and combined queries

- [x] Store image vectors separately from the existing text vectors, with model identity and media/evidence/entity references.
- [x] Implement image queries and image-plus-text queries through the existing `search` command and filters.
- [x] Combine rankings from compatible retrieval channels; never directly add embeddings from unrelated models.
- [x] Return one result per entity with matching media IDs, source pages, and the contributing retrieval channels.
- [x] Keep diverse image views and prevent duplicate or numerous images from dominating entity rankings.
- [x] Add media inspection/export and embed compact previews within the distribution budget, keeping originals in the local archive.
- [x] Extend bundle validation and `verify` to check image vectors, media references, and cross-runtime probes.

Complete when a local photograph retrieves the expected pilot entities offline and the
researcher can inspect the exact supporting image. Existing text commands must still pass.

Implemented query interface:

```sh
pipelines search --image photograph.jpg
pipelines search --image photograph.jpg "truck-mounted"
pipelines search --image photograph.jpg --kind radar --limit 5
pipelines media SOURCE:ID
pipelines media --id SOURCE:media:HASH --output preview.jpg SOURCE:ID
```

Implementation checks: 426 Python tests, Go tests/vet, lint, types, and Python package
build pass locally. Format 3 adds the optional image model, float16 vectors, compact
previews, and evidence associations; format 2 text bundles remain supported. Combined
queries use equal reciprocal-rank fusion with constant 60. Image scores use the best
view per entity, with at most eight selected views per entity and a 32 MiB preview cap.

Image and combined responses return ranked suggestions with `no_supported_match`
and `calibration_status: uncalibrated`. The frozen development set lacks negatives
needed to establish an acceptance threshold. Keep ranked retrieval metrics separate
from accepted matches; threshold calibration and broader quality gates remain M7/M10.
The restricted nine-image bundle preserves all 9,440 text members byte-for-byte.
Windows and Linux `verify` pass four text and three image probes; Linux ran with
networking disabled. Linux command acceptance also passes source exports from all
11 sources, image and combined queries, exact preview export, and overwrite rejection.
All 116 text cases retain their M1 ranks: 73/73 required cases, 101/116 first,
110/116 within five, and no skipped sources. Standard CI passes at `9bb1574`.

The development images rank their expected entity first in 6/6 cases. The executable,
gallery, model, and fixed fusion choice were frozen before held-out evaluation:
8/9 positive image cases rank first (also 8/9 within five; seven photo groups), and
2/2 combined cases rank first, both globally and with source filters. The stowed Lanza
truck ranks ninth globally from its image alone and first with its supplied text.
All responses remain uncalibrated suggestions, including the single sky negative;
accepted-positive accuracy is therefore zero. The two diagram cases remain unscored.
This small gallery does not establish full-archive or release quality.

Reports: `build/m5/development-cli.json`, `search-selection.json`, `evaluation-cli.json`,
`text-regression.json`, `text-member-comparison.json`, and `offline-linux.json`.

Full pilot bundle `ec7093d263154d3b725cf6fd7bfe585f46193ba77b9a8751138fc8ca41488d07`
contains 1,055 unique indexed views across 280 entities (Commons: 633 views / 148
entities; Military Periscope: 422 / 132). It accounts for all 2,799 saved files:
1,055 indexed, 1,149 alternate resolutions, and 595 excluded by the view cap. No
entity exceeds eight indexed views. Previews total 29.59 MiB; original files remain
in the local archive. The full gallery includes evaluation photos, so only the
restricted gallery above supports held-out measurements.

The full Windows and Linux builds pass command acceptance and all seven embedded
probes; Linux runs with networking disabled. A cached rebuild reports `changed:false`
and the same dataset identity (101 s versus 625 s initially). Local Windows artifacts:
`build/m5/pipelines.exe` (237.93 MiB) and `build/m5/pipelines-windows-amd64.zip`
(227.91 MiB), within the 320/256 MiB limits. Archive audit, repeat-build, export,
and size reports are saved under `build/m5/`.

CI: [standard checks](https://github.com/osint-builders/pipelines/actions/runs/35632791612)
and [all five native encoder checks](https://github.com/osint-builders/pipelines/actions/runs/35632791585)
pass at `9bb1574`.

Full pilot measurements on the M1 Windows hardware, with one first observed process
and 20 subsequent fresh processes per mode (filesystem caches were not cleared):

| Query | First process | Repeat p95 | Peak working set |
| --- | --- | --- | --- |
| Text | 2.094 s | 2.101 s | 873.8 MiB |
| Image | 2.216 s | 2.246 s | 764.2 MiB |
| Image and text | 4.090 s | 4.070 s | 990.4 MiB |

All local resource limits pass. The repeated text probe is 5.9% slower than M1,
and its peak memory is 10.5% above M1's measured maximum, within the 20% limits.
These are local probe measurements; full native resource gates remain M10.
Details: `build/m5/performance.json`. No release was published. The user's continuation
verified M5 and authorized M6.

## M6 — OCR and visual descriptions

- [x] Extract text from markings, labels, and diagrams while retaining image regions and extraction confidence where available.
- [x] Generate concise visual descriptions during dataset construction, with the model and processing recipe recorded.
- [x] Store generated observations separately from source statements and structured specifications.
- [x] Index OCR and descriptions as evidence-linked text, with their origin visible in search results.
- [x] Reuse unchanged analysis and verify that OCR/descriptions improve held-out retrieval without increasing false identification.

Complete when visual details become searchable through text and every derived statement
can be traced to an image. Heavy analysis models run during construction, not CLI queries.

Implemented `pipeline-build observe`, format-4 bundles, `search --observations TEXT`,
and `observations SOURCE:ID`. Default source-only search is preserved. Generated matches
carry their origin, image, captured evidence, model revision, and processing recipe;
descriptions have no invented confidence score. Uncalibrated generated queries return
ranked suggestions with `no_supported_match`.

Build-time engines: pinned RapidOCR detector/orientation/Cyrillic recognizer graphs
(18.59 MB) and Qwen3-VL-2B-Instruct (4.27 GB). The CLI embeds only the generated text,
provenance, and MiniLM vectors. Six gallery images informed caption selection; occasional
unsupported details remain. OCR can return publisher credits or background text and
confuses some Latin/Cyrillic markings. These strings do not become source facts or aliases.

The restricted nine-image gallery produced 18 observations with no failures or network
attempts. Its format-4 CLI passed four source-text, three image, and three observation
embedding probes. All 9,458 previous bundle members retain identical hashes; the six
new members add 44.0 KiB compressed. Full analysis of the 1,055 indexed pilot views is
running using two local GPUs.

The 23 new manual query cases were frozen before retrieval results; their author had seen
engine pilot summaries, so this is a provisional comparison rather than a blinded study.
Fixture hashes, photo groups, derivatives, and gallery isolation passed an independent
audit. Seven development queries cover four photo groups; ten held-out positive queries
cover seven groups. Two development and four evaluation negatives are easy controls.

| Query split and scope | Source top 1 / within five | With observations top 1 / within five |
| --- | --- | --- |
| Development, global | 0/7 / 0/7 | 1/7 / 2/7 |
| Development, source filtered | 1/7 / 1/7 | 3/7 / 3/7 |
| Evaluation, global | 2/10 / 3/10 | 7/10 / 7/10 |
| Evaluation, source filtered | 2/10 / 5/10 | 7/10 / 9/10 |

Five of seven held-out visual-description queries improve to first place; the three
OCR queries show no net gain. No held-out positive rank worsened. All generated queries
remain `no_supported_match`, including positive queries: zero accepted identifications
or false acceptances is an abstention policy, not measured identity calibration.
The global 7/10 ranked result has a wide 95% Wilson interval, 39.7–89.2%.
All 116 original text ranks are unchanged, including 73/73 required cases first.

Implementation `3bb8c29` passes Python/Go checks and both GitHub workflows
(`35638557693`, `35638557759`); image-encoder parity passes on all five native targets.
Reports are under `build/m6/`: `fixture-audit.json`, `development.json`, `selection.json`,
`evaluation.json`, `text-comparison.json`, and `member-equivalence.json`.
Full coverage: 1,055 descriptions and 639 nonempty OCR observations across 280 entities;
416 OCR analyses return no text. Commons supplies 633 descriptions/228 OCR observations,
Military Periscope 422/411. All 2,110 image/analysis outcomes are accounted for without
failures. The two GPU workers completed in 28.1 and 29.3 minutes. A full rerun with
inference disabled reused every result in 15.5 seconds with zero network attempts.
These full-gallery counts establish coverage, not held-out retrieval quality.

The full format-4 dataset is
`a32527cad620258365dc2479b68c8c9e55dd3cbb61deb69035c567fe598a6b29`:
1,694 observations, 1,727 generated text chunks, and all 4,337 source entities retained.
All 10,504 previous source/image/model members have identical hashes. Observation
sidecars add 2.93 MiB compressed. The Windows executable is 240.94 MiB and its
single-binary ZIP is 230.87 MiB, within the frozen distribution budgets.

Windows and Linux full-bundle acceptance passed, including exact generated-content
inspection, source exports, opt-in text/combined queries, and an independent source-vector
ranking check. Linux ran with networking disabled, a read-only filesystem, and no added
capabilities. All four source-text, three image, and three observation embedding probes pass.

Windows reference measurements, first observed process plus 20 fresh processes per mode,
without concurrent build, test, or analysis jobs from this task:

| Query mode | First / repeated p95 | Peak working set |
| --- | --- | --- |
| Default text | 2.111 / 2.140 s | 879.99 MiB |
| Text with observations | 3.012 / 3.014 s | 1,018.89 MiB |
| Image + text with observations | 4.230 / 4.321 s | 1,038.99 MiB |

Default text is 7.9% slower and uses 11.2% more peak memory than M1, within its 20%
regression allowance. Relative to M5, those changes are 1.9% and 0.7%. All measured modes
fit the 2 GiB absolute memory budget. Opt-in text exceeds the 3 s p95 target by 0.014 s
and uses 28.8% more peak memory than M1, exceeding the 20% text-memory allowance.
Those opt-in resource gaps remain release work in M10; no release gate is claimed passed.
Other native platforms still need full-binary resource measurements in M10.

Local review artifacts: `build/m6/pipelines.exe`, `pipelines-windows-amd64.zip`,
`pipelines-linux`, and `full-dataset.zip`. Evidence in the same directory:
`coverage.json`, `full-analysis-report.json`, `full-package.json`,
`windows-acceptance.json`, `linux-acceptance.json`, `distribution-size.json`,
`performance.json`, and `resource-gates.json`. The user verified M6 and authorized M7.

## M7 — Better text and combined ranking

- [x] Add full-text lexical ranking alongside semantic search, preserving exact designation and alias matches.
- [x] Index original names, abbreviations, reviewed alternate names, captions, and OCR without converting incidental mentions into aliases.
- [x] Select rank fusion on development cases, freeze it before evaluation, and keep text, image, and combined query behavior explicit.
- [x] Test partial names, spelling variations, specification phrases, and queries with no supported match.
- [x] Expose useful match reasons and treat similarity scores as ranking signals rather than identity probabilities.

Complete when the frozen text baseline has no unacceptable regression and the combined
retrieval targets pass. Keep exact vector search until measurements justify another index.

Implemented BM25 over existing source chunks and complete source names,
plus 1,689 associated source captions. Generated observations remain opt-in. Unicode
normalization and conservative name-only spelling tolerance do not create aliases or
equate numeric variants. Hybrid text combines lexical and semantic entity ranks;
source-name matches retain priority. Image/text fusion still receives one text rank,
regardless of how many lexical or generated evidence fragments are present.

The new source-caption member is 503,467 bytes uncompressed; existing source text,
vectors, images, and observations are unchanged. The isolated benchmark includes only
its nine gallery captions. The 44-query text fixture was frozen before retrieval at
`bf589924cabe3e6b0ffc8613a35302e3ab428d7492b59e5846fac7275ed1a791`.
Each split has 18 positive and four negative queries, reported globally and with source
filters. Development-only comparison selected lexical weight 2, semantic weight 1,
and rank constant 60: global first/within-five improved from 15/18 and 16/18 to
16/18 and 18/18; filtered results improved from 16/18 and 17/18 to 17/18 and 18/18.
No positive development rank worsened. The actual CLI reproduced these results and
all 116 frozen baseline ranks, including 73/73 required first-place results. Windows
and network-disabled Linux acceptance passed. The public packager reproduced the
complete bundle byte-for-byte in 112.3 seconds.

Six paired development photographs with source-backed text constraints ranked an
expected entity first for both M6 and M7, globally and with source filters. All six
image-only results also stayed first. Opt-in observation development top-five results
improved from 2/7 to 3/7 globally and 3/7 to 4/7 filtered; no previous top-five success
was lost. Choices and executable hashes are frozen in `build/m7/ranking-selection.json`,
`image-selection.json`, and `observations-selection.json`; held-out runs are complete.

Each hybrid result exposes its text ranking, raw semantic/BM25 contributions, matched
terms, and source or generated provenance. `name_match` means a complete normalized
source-name span, not an identity probability. A default hybrid query whose first
suggestion lacks both lexical and source-name support returns `no_supported_match`.
Generated and image queries retain their existing uncalibrated abstention behavior.
Overlapping words can still produce candidates for absent entities: the four global
development negatives all did so, and two of four did so with source filters. Broader
abstention calibration remains an explicit M10 requirement.

Held-out ranking results with the frozen choice:

| Query set | M6 first / within five | M7 first / within five |
| --- | --- | --- |
| New text, global | 15/18 / 16/18 | 16/18 / 18/18 |
| New text, source filtered | 15/18 / 16/18 | 16/18 / 18/18 |
| Observation text, global | 7/10 / 7/10 | 7/10 / 8/10 |
| Observation text, source filtered | 7/10 / 9/10 | 9/10 / 10/10 |
| Image/text, global and filtered | 2/2 / 2/2 | 2/2 / 2/2 |

No new text rank worsened in either scope; three improved. The existing image-only
evaluation ranks are unchanged (8/9 first and within five). These are small pilot
results: text top-five 18/18 has a 95% Wilson interval of 82.4–100%, and image/text
2/2 has an interval of 34.2–100%. The observation/image fixtures were evaluated in
earlier milestones and serve as regressions, not newly blinded evidence.

Text negatives produced candidates for 4/4 global and 3/4 filtered queries, versus
4/4 in both scopes before. Generated queries accepted 0/10 positives and 0/4 negatives;
image queries also remain uncalibrated. These results do not pass the release-sized
quality/calibration gates. Reports are in `build/m7/ranking-evaluation.json`,
`image-evaluation.json`, and `observations-evaluation.json`.

Caption evidence resolves only for returned
results; `verify` still checks every caption association and source URL. An exhaustive
comparison against the frozen implementation found identical lexical indexes and
byte-identical output for all 1,698 full/benchmark caption matches. The CLI prepares
the text index before allocating its query model and closes the model after encoding.
Ranking parameters, model weights, and dataset members remain frozen. All 12 complete
query responses are byte-identical before and after this memory optimization, including
captions, observation text, and combined queries. Frozen evaluation reports retain their
original executable hashes; `build/m7/final-parity.json` links them to the final builds.

Implementation `0d20f4a` passes all six [CI jobs](https://github.com/osint-builders/pipelines/actions/runs/35653054532).
The final Windows and Linux full builds pass all ten embedded probes and command
acceptance, including exports from every source, source-caption provenance, observation
queries, and media export. Linux ran without networking, with a read-only filesystem
and no added capabilities. Reports: `build/m7/windows-acceptance.json`,
`linux-acceptance.json`, `caption-equivalence.json`, and `final-parity.json`.

Full dataset: `1246b384423a929363d68c67f62d3aff222eb58f2ad74d83edb3911cd4a3eaa6`.
Restricted benchmark: `34565499e8f3201d254d2731d44bfb68b634f806815195c9ae33da45c4f71a39`.
Windows reference measurements use one first observed process and 20 subsequent fresh
processes per mode, with no concurrent build/test/analysis jobs from this task and
without clearing the filesystem cache:

| Query mode | First / repeated p95 | Peak working set |
| --- | --- | --- |
| Default text | 2.641 / 2.662 s | 936.55 MiB |
| Text with observations | 3.532 / 3.556 s | 1,070.64 MiB |
| Image + text with observations | 4.762 / 4.814 s | 1,011.20 MiB |

Default text passes the 3 s absolute target and uses 18.4% more peak memory than M1,
within the 20% allowance. Its p95 is 34.2% slower than M1, exceeding the relative
latency allowance. Observation text exceeds both the absolute/relative latency and
relative memory budgets. Combined queries pass their measured resource limits, and
all modes remain below 2 GiB. These gaps and other native measurements remain M10.

The standalone Windows executable is 241.10 MiB; its single-binary ZIP is 231.01 MiB,
within the 320/256 MiB distribution limits. Review artifacts: `build/m7/pipelines.exe`,
`pipelines-windows-amd64.zip`, `pipelines-linux`, and `full-dataset.zip`. Final hashes,
measurements, and gate outcomes are in `distribution-size.json`, `performance.json`,
and `resource-gates.json` in that directory. No release was published. The user's
continuation verified M7 and authorized M8.

## M8 — Entity relationships and precise filters

- [x] Link equivalent records across sources while preserving every source-qualified ID and its evidence.
- [x] Represent family, variant, component, and related-system relationships distinctly from equivalence.
- [x] Add evidence-backed manufacturer, country, date, and specification fields, preserving original values, units, qualifiers, and unknowns.
- [x] Add deterministic filters and numeric comparisons over normalized fields; distinguish publication/capture dates from dates describing the entity.
- [x] Provide machine-readable relationship and comparison output suitable for research scripts and teaching material.
- [x] Validate ambiguous names and visually similar variants so incorrect merges do not become permanent identities.

Complete when researchers can follow a subject across sources, distinguish variants,
and reproduce filtered results without losing the original records.

Implemented an optional research extension with shared normalization and source-owned
field mappings/relationship assertions. Original records, vectors, images, captions,
and generated observations stay byte-identical. The pilot adds 18,723 mapped source
facts and 5,693 capture-date claims, plus 13 reviewed relationships: six equivalents,
two family memberships, one qualified prospective variant, two components, and two
related systems. Every relationship has exact retained quotes from both endpoints;
none merges IDs, propagates facts, or creates transitive equivalence.

New JSON commands:

```sh
pipelines facts SOURCE:ID
pipelines relationships --type equivalent SOURCE:ID
pipelines compare SOURCE:ID OTHER:ID
pipelines list --where "manufacturer=Thales"
pipelines search --where "origin_country=France" --where "mass>=10 t" "vehicle"
```

Repeated `--where` predicates also apply to similar, image, and observation queries
before ranking. Text comparisons preserve punctuation; numeric comparisons require
compatible units and the complete asserted interval. SI symbol case matters (`mW`
differs from `MW`). Unknown, approximate, and unparsed values do not satisfy strict
filters, and one claim must satisfy every predicate for a given field. Date precision
is retained: `>=2000` starts at January 1 and `<=2000` ends at December 31.
Manufacturer/contractor and origin/designer/operator/site countries remain distinct.
Source-reported IOC may be planned; its development status remains a separate claim.
Capture and publication/update dates never become equipment events.

The 29-field catalog preserves 24,416 claims: 19,101 known, 886 unknown, 4,426 unparsed,
and three approximate. These statuses describe parsing, not independent verification
of a source's claim. Source mappings cover 18,723 original facts across nine sources;
Commons and ClimateViewer retain their original records and capture-date claims.
Relationships cover 13 selected reviewed pairs, not exhaustive corpus-wide linking.
Missing fields remain explicit unknowns in comparisons; original `get` output is intact.

Implementation `beafc67b` passes all six [CI jobs](https://github.com/osint-builders/pipelines/actions/runs/35656910425)
and [encoder checks on all five native targets](https://github.com/osint-builders/pipelines/actions/runs/35656910463).
Windows Python tests: 777 passed, one skipped; Linux: 776 passed, two skipped.
The 63-command source evaluation verifies 15 fact cases, 15 filter cases, all 13 links
from both endpoints, 36 exact archived quotes, eight ambiguous negatives, and 196
explicit unknown comparison cells. Repeated responses, stable IDs, raw facts, and
evidence locators match. All 116 baseline ranks and first-result IDs remain unchanged,
including 73/73 required results first. Twelve complete default query responses match
M7 apart from the dataset ID, including image, combined, and observation modes.

The public packager reproduces the complete bundle byte-for-byte (`changed: false`);
all 10,511 original M7 members remain unchanged. Windows and Linux full-bundle
acceptance and all ten embedded probes pass. Linux ran without networking, with a
read-only filesystem and no added capabilities. Query-free research commands need
no model inference. Existing bundles without the research extension remain supported.

Full dataset: `a31e79c211177bf1f04a6d45114104e6bbb84d0982cc90816a83dec1b84c9389`.
Restricted benchmark: `44f5dc64a18ccdb2479db08811f7394fea70ec1db9671adef42ba67e1233e0fe`.
The Windows executable SHA-256 is
`d92dbbd8f46cbb3cb9df6749739c30e568f80fe2681819730397e27af664a2ce`.
Windows reference measurements use one first observed process and 20 subsequent fresh
processes per command, without concurrent build/test/analysis jobs from this task.
The filesystem cache was not cleared; these are complete process launches, not a
resident service or reboot-cold measurement.

| Command | First / repeated p95 | Peak working set |
| --- | --- | --- |
| Default text, same M1/M7 query | 2.618 / 2.683 s | 943.58 MiB |
| List with `manufacturer=Thales` | 1.118 / 1.150 s | 707.46 MiB |
| Text with `manufacturer=Thales` | 3.410 / 3.406 s | 1,073.47 MiB |

Default text changes by less than 1% from M7 in latency and memory. Its memory is
19.3% above M1, within the 20% allowance; p95 remains 35.3% above M1 and fails the
relative latency budget. Filtered text exceeds the 3 s absolute p95 limit. Its query
differs from the M1 probe, so no relative latency/memory comparison is claimed.
All measured commands fit the 2 GiB absolute memory limit. List has no frozen
mode-specific latency target. These gaps join the existing M7 release work in M10.

The standalone Windows executable is 242.92 MiB; its single-binary ZIP is 232.65 MiB,
within the 320/256 MiB limits. Review artifacts are in ignored `build/m8/`:
`pipelines.exe`, `pipelines-windows-amd64.zip`, `pipelines-linux`, and `full-dataset.zip`.
Validation reports in that directory: `evaluation.json`, `default-parity.json`,
`text-regression-comparison.json`, `public-package.json`, `windows-acceptance.json`,
`linux-acceptance.json`, `ci.json`, `claim-coverage-final.json`, `performance.json`,
`distribution-size.json`, and `resource-gates.json`. No release was published.
The root README remains scheduled for M10. The user's continuation verified M8 and
authorized M9.

## M9 — Media coverage across all sources

- [x] Complete the rollout table below using the shared interfaces; keep adapters limited to website-specific behavior.
- [x] Audit original image resolution, captions, entity associations, duplicates, and failed captures for each source.
- [x] Build image vectors and derived text for applicable media and publish per-source coverage metrics.
- [x] Verify unchanged media reuse cached analysis and vector output on repeat builds.
- [x] Require an explicit outcome for sources or records with no applicable images; avoid substituting logos or unrelated pictures.

Complete when every source has an audited media capability and the full dataset can be
rebuilt from local captures. Each source must account for outstanding failures.

Nine additional source-owned adapters now implement the shared media interface. Discovery
uses saved responses and exact entity/evidence associations; downloads retain original
or preview roles without guessing larger image URLs. Shared coverage reports distinguish
saved, incomplete, and no-applicable-image records, with resolution, caption, duplicate,
and failure counts. Bulk registration writes each URL once while preserving occurrences.
Rich occurrence references prevent excluded captions from entering image or text indexes.
Source text, reviewed relationships, and the frozen evaluation gallery remain preserved.

Implementation `62d438c` passes 822 local Python tests, lint/format/type checks,
all six [CI jobs](https://github.com/osint-builders/pipelines/actions/runs/35660734830),
and [image encoder checks on all five native targets](https://github.com/osint-builders/pipelines/actions/runs/35660734788).
Final archive audit verifies all 11 unchanged source snapshots, 4,324 archived responses,
and 9,674 unique image blobs (3,380,509,962 bytes). All 9,696 saved image records are
represented. Captured media covers 3,103 entities; 915 entities have no applicable images.
There are no pending downloads. The 68 failed captures remain explicit: 44 Commons
originals retain saved previews (41 unsupported formats, two invalid files, one HTTP 429),
20 ClimateViewer links fail (11 network errors, nine HTTP 404), and four RadarTutorial
links fail (two HTTP 404, two MIME mismatches). Uncertain associations stay marked.

The offline bundle indexes 1,455 unique image vectors covering 1,101 entities within
32 MiB of previews. Distribution records 4,206 preview-budget exclusions, 973 view-budget
exclusions, and 102 unsupported-format exclusions; other resolutions remain linked.
Local originals and recorded previews are retained independently of the CLI gallery.

Both analyses covered every indexed record: 1,455 descriptions and 328 OCR observations,
with 1,128 empty OCR outcomes and no analysis failures or skips. A strict cached rerun
completed without fresh OCR/description inference or network connection attempts.
All 9,442 protected bundle members match M8 byte-for-byte, including source facts,
evidence, text models/vectors, and research relationships; the frozen seed is untouched.
Nine source-filtered CLI image roundtrips return the expected entity first and preserve
exported preview bytes and evidence links. These are functional checks using gallery
images, not held-out retrieval-quality measurements. The public cached packager returns
`changed: false` and the identical ZIP checksum with no captured-image or corpus-text
inference, encoding only three image and three text probes. Windows and Linux full-bundle
acceptance passes, including all ten embedded probes; Linux ran without networking,
with a read-only filesystem and no added capabilities. All 116 baseline ranks and first
results match M8 and M1: 73/73 required first, 101/116 first, 110/116 within five, and
MRR 0.9018162871611147, with no skips.

| Source | Saved records | Entities with media | Indexed records | Descriptions / OCR |
| --- | ---: | ---: | ---: | ---: |
| armyrecognition | 241 | 11/11 | 24 | 24 / 15 |
| cambridgepixel | 0 | 0/385 | 0 | 0 / 0 |
| climateviewer | 0 | 0/383 | 0 | 0 / 0 |
| commons | 2,372 | 148/151 | 170 | 170 / 61 |
| deagel | 2,615 | 943/1,285 | 590 | 590 / 51 |
| fandom | 45 | 12/46 | 2 | 2 / 0 |
| militaryperiscope | 427 | 132/143 | 98 | 98 / 94 |
| radartutorial | 3,745 | 1,732/1,735 | 543 | 543 / 96 |
| russianforces | 16 | 7/57 | 2 | 2 / 0 |
| virtualglobetrotting | 204 | 100/100 | 23 | 23 / 11 |
| wikipedia | 31 | 18/41 | 4 | 4 / 0 |

Image bytes shared across sources count once in the global vector/observation totals.
Detailed resolution, caption, duplicate, association, and failure metrics are retained
per source and entity in ignored `build/m9/coverage*.json`.

Windows measurements use one first observed process and 20 subsequent fresh processes
per command, after all other task workloads finished. Filesystem caches were not cleared.
All repeated responses are identical.

| Command | First / repeated p95 | Peak working set |
| --- | --- | --- |
| Default text | 2.692 / 2.708 s | 960.86 MiB |
| Image | 2.698 / 2.683 s | 826.49 MiB |
| Image + text | 4.997 / 5.088 s | 1,063.39 MiB |
| Text with observations | 3.902 / 3.938 s | 1,144.79 MiB |
| Text with manufacturer filter | 3.480 / 3.424 s | 1,080.79 MiB |

Image and combined modes pass their absolute limits. Every mode fits the 2 GiB memory
limit and its first-process latency limit. Default text passes 3 s absolute p95 but
exceeds M1's 2.380 s relative target and 949.30 MiB relative memory allowance. Its p95
and memory rise 0.9% and 1.8% from M8. Observation-assisted and filtered text exceed
the 3 s absolute p95 limit; observation-assisted text also exceeds both M1 relative
limits. These remain explicit M10 work; release quality/resource gates are not complete.

Review artifacts in ignored `build/m9/`: `pipelines.exe` (247.36 MiB),
`pipelines-windows-amd64.zip` (237.07 MiB), `pipelines-linux`, and `full-dataset.zip`.
The executable and single-binary ZIP fit the 320/256 MiB budgets.

Dataset: `e261f04023a83372ef054f17ccd262de292272cdc5c8bec96f20c8b5c86a4416`.
Bundle SHA-256: `86f8d5a1942d08f516db6b8e7ebf3e2a139247acb6ac55afe3067e959d0b124e`.
Windows executable SHA-256:
`775ae6e7ce009cc00eb287f5f9845b8e59ee3acf3c6cd506f5f7f91f9e2b2c81`.
Reports: `coverage*.json`, `member-preservation.json`, `public-package.json`,
`gallery-cached-analysis-report.json`, `image-roundtrip.json`,
`text-regression-comparison.json`, `windows-acceptance.json`, `linux-acceptance.json`,
`performance.json`, `resource-gates.json`, and `distribution-size.json`.
The user's continuation verified M9 and authorized M10. No release was published.

## M10 — Quality gates and compact releases

Work is split across runtime profiling, quality/coverage evaluation, release tooling, and
compact gallery allocation. M9 artifacts and frozen query/seed fixtures remain intact.
The current held-out seed has seven positive photo groups, below M1's release minimum
of 100; expanded reviewed inputs and valid calibration remain required before release.

The user has no existing reviewed image-query set. A local source-grounded review
queue now contains 508 candidate entities; candidate duplicate groups and captions
require visual review before any new split or ground-truth claim. It does not replace
the frozen seed or establish release quality.

Runtime profiling identified a redundant embedded-bundle copy and ZIP inflation of
models/vectors. ReaderAt loading removes the copy; storing binary members without
inner compression removes repeated inflation while the downloadable ZIP still
compresses them. Diagnostic samples preserve all 15 M9 JSON responses and pass all
ten embedded probes: text 1.162–1.176 s, image 1.658–1.671 s, combined 2.765–2.783 s,
observation text 2.073–2.127 s, and filtered text 1.846–1.866 s. Maximum observed
memory is 743 MiB and compressed size 237.46 MiB. Final isolated/native measurements
remain required; these three-run diagnostics are not the release resource report.

Preview allocation now preserves its round-robin entity order under the byte budget.
Development-only comparison and visual inspection selected full-view 320-pixel JPEG
previews at quality 65: six previews use 66,873 bytes versus 175,190 at 512/75.
Their original-to-preview embedding cosine averages 0.944 (minimum 0.928); this
measures exported-preview changes, not held-out retrieval. Original media, vectors,
and OCR inputs remain intact. Preview settings and ZIP storage enter recipe identity
and invalidate the appropriate cached build. Full-gallery coverage is being rebuilt.

- [ ] Run the frozen evaluation suite for text, image, combined, OCR, filters, and entity relationships; report results by task and source.
- [ ] Verify top-result accuracy, recall within the first five results, confusable variants, and no-match behavior meet M1's targets.
- [ ] Calibrate no-match decisions on larger development sets: M7 text negatives return candidates for 4/4 global and 3/4 filtered queries; image/generated modes currently abstain even for positive queries.
- [ ] Measure cold/warm latency, peak memory, and compressed executable size on all supported targets.
- [ ] Bring text within the frozen budgets: M9 default p95 is 2.708 s against M1's relative limit of 2.380 s and uses 960.86 MiB against 949.30 MiB; filtered text is 3.424 s against 3 s absolute; observation text is 3.938 s and uses 1,144.79 MiB.
- [ ] Build and test one standalone executable per platform with required models, indices, evidence, and selected previews embedded.
- [ ] Improve gallery coverage within the preview budget: M9 retains 9,696 saved records but embeds 1,455 views covering 1,101 entities; compare preview size and allocation using development data.
- [ ] Validate deterministic dataset identities, cached rebuilds, checksums, source exports, and operation without network access.
- [ ] Extend release change detection and tag identity to include image artifacts; the existing gate compares text content only.
- [ ] Run lint, types, unit/integration tests, Python/Go parity, and native release acceptance checks.
- [ ] Update the root README with only shipped CLI options/API behavior and the source table.
- [ ] Publish the verified binaries and checksums through the existing manual release process.

Complete when the released CLI satisfies the agreed quality and distribution budgets
and its results identify the supporting source and media evidence.
