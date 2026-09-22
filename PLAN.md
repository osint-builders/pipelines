# Entity search milestones

Help researchers and educators find static entities quickly using names, descriptions,
specifications, and images, with every result linked to captured evidence.

**Progress:** 9/10 milestones verified. **Active:** M10 in progress.

**Branch:** `main`.

## Cambridge Pixel refresh — in progress

Reconcile the supplied 388-row HTML table with the previous 385-record archive
and the live 393-row catalog. Preserve every field and stable identities when
manufacturer names change. Review Google Images results one radar at a time;
attach only images supported by model-specific source evidence.

- [x] Parse and reconcile every supplied row, including applications and references.
- [x] Capture the updated catalog and verify complete offline extraction.
- [ ] Review imagery for each model; retain search, original-page, and match evidence.
- [ ] Download matched originals through the shared media archive and audit coverage.
- [ ] Rebuild the local index, test the CLI, update README counts, and publish a new latest release.

Working reports: `build/cambridgepixel/`. All 388 supplied models match the refreshed
393-record catalog; five additions and one application-list change are accounted
for. Seven renamed entries retain their existing IDs. Referral parameters no longer
cause false table/schema disagreements; functional URL differences still fail.
Catalog archive: `20260922T142532Z-932ca3c5`. Image reviews are stored beside the
adapter in `image_reviews.json`; matched originals use the shared media archive,
with separate captured source evidence. Browser DOM captures are explicitly labelled.
The first 185 models have been reviewed and audited: 174 image URLs saved for 179
matched records, six unresolved matches, and no failed or pending downloads.
Ten associations are explicitly marked ambiguous. Individual review has continued
through MPN-14K (187 decisions). Garmin images retain selected part
numbers, sizes, and power configurations; GC's X5-21FH was verified in its hardware
catalog after Google returned unrelated vehicle parts. SuperNet and SuperNet SSR
retain separate identities and image evidence despite sharing a manufacturer page.
Easat's inconsistent EA40575/EA45075 designation remains explicit. Four BAE, three
BEL, one Foxtrack, four ICS, two IAI, and two Indra images were exported from browser assets and
validated by the shared image store. Foxtrack's public article capture excludes
account controls. Source URL spaces and encoded spaces compare correctly without
collapsing reserved path characters.
HADES gallery originals are validated through linked image roles; plain links
still fail image-evidence validation. Hensoldt originals retain the gallery DOM,
with separate Mk11 and IFF images resolving reversed Cambridge reference links.
TRML and TRS variants have separate images; Hikvision ranges retain model-specific
evidence and manufacturer/catalog status differences. Radartutorial enlargement
links beside thumbnails are supported, and rendered Hikvision image URLs are
preserved in browser captures. Legacy ELM-2129 imagery is explicitly related ARSS
context, supported by the archived model cross-reference.
JRC scanner tabs retain labeled antenna options and dimensional drawings. Koden's
MDC catalog entry links to MDS; separate manufacturer model pages supply the image
evidence. ICx STS-12000 remains unresolved. Indra's shared L/S-band illustration
is explicitly ambiguous. Kongsberg DR100 retains its full-screen gallery capture;
MPN-14K retains an individually captioned Air Force museum photograph.
Validation: 88 focused tests passed, including poster images, linked
originals, responsive images, gallery thumbnails, CSS backgrounds, and ambiguous associations.
Explicit image download types are supported; ordinary links and non-image CSS fail.
All 393 records and 2,700 source facts pass the current capture audit.

## Code cleanup — complete

Apply behavior-preserving changes in small tested commits on main. Existing release
artifacts and M10 benchmark inputs remain unchanged.

- [x] Reuse content/evidence identity validation and remove the duplicate numeric validator; 163 focused tests and the released dataset verification passed.
- [x] Consolidate shared Go bundle validation and evidence ownership; all Go tests and vet passed.
- [x] Unify repeated scraper media-owner lookup while retaining source-specific matching; affected media and pipeline tests passed.
- [x] Consolidate repeated HTML link rewriting without changing source policies; 100 source and pipeline tests passed.
- [x] Run the complete checks and commit the finished cleanup.

Validation: 1,150 Python tests, all Go tests/vet, full Ruff/mypy, and wheel/source
builds passed. The released dataset still verifies with the same identity; a fresh
CLI build passes integrity/model checks and matches the released CLI byte for byte
on 15 commands covering search, filters, similarity, exports, facts, relationships,
and comparisons. Reports remain in ignored `build/cleanup/`. M10 is still in progress.

## ODIN source capture — ready for verification

Requested alongside M10: archive the complete Worldwide Equipment Guide catalog and
its pictures through the shared source interfaces. M10's frozen benchmark remains
unchanged. The supplied API advertised 4,118 live equipment records and responded
without a session cookie. Stable identifier pagination was reconciled against that
total and the source's complete equipment-type hierarchy.

- [x] Add the ODIN adapter, full nested specifications, shared POST crawling, media discovery, and source audit.
- [x] Capture every equipment page and category definition; verify no missing or duplicate IDs.
- [x] Download all linked equipment pictures and account for every outcome.
- [x] Verify offline replay, source/media audits, tests, and local entity-index access.
- [x] Record final counts and any source-side gaps; push the source changes.

Capture `20260922T031929Z-df120f6c`: 42 catalog responses plus the category hierarchy,
4,118 unique equipment records, 222 populated equipment types, 29,583 nested sections,
and 166,232 source properties. All records declare imagery: 11,838 references to
11,818 distinct URLs. Independent coverage: `build/odin/full-coverage-review.json`.
Offline source audit passed: 207,362 retained facts, 392,630 content fragments,
and every image reference (`build/odin/source-audit.json`). The complete raw response
and rendered-evidence checksums also pass (`build/odin/artifact-checks.json`). The
capture exposed and fixed a shared JSONL reader issue with literal Unicode separators.
Media review identifies four records with only placeholder-labelled images and six
source-association groups for later visual review (`build/odin/media-review.json`).
The local text index and single-executable Windows CLI now include all 8,455 entities
across 12 sources (`build/odin/dataset.zip`, `build/odin/pipelines.exe`). Four ODIN
queries rank their expected aircraft, vehicle, vessel, and radar first; JSON, HTML,
Markdown, and source exports match the bundle (`build/odin/cli-checks.json`).
Media capture saved all 11,818 URLs as 11,758 distinct original files (1.95 GB); all 4,118
entities have saved media, with zero failed, pending, excluded, or unassociated URLs.
Two broken proxy responses were recovered through matching direct asset routes using
a bounded shared fallback; original URLs and evidence references remain intact.
Generic MIME headers, static GIFs, and MPO originals retain their unchanged bytes.
One MPO has a valid primary picture but an absent secondary thumbnail; frame
validation metadata records that source defect. GIF/MPO originals are archived and
explicitly excluded from image vectors and previews.

The ODIN build currently provides text search; its downloaded originals remain in
the shared local media archive. No M10 image-search calibration or release gates
were changed. The stripped Windows executable is 295.67 MiB, or 270.91 MiB as a
single-executable ZIP (`build/odin/pipelines-windows-amd64.zip`, `SHA256SUMS`).
Validation: 1,147 Python tests, full Ruff/mypy, Go tests/vet, CLI integrity and model
checks, and four ODIN query/export checks passed. Final source/media audit:
`build/odin/audit.json`; generated artifacts remain outside version control.

The user requested direct GitHub publication of the current text/research CLI after
the ODIN capture. Packages target Windows amd64, Linux amd64/arm64, and macOS
Intel/Apple silicon, with a matching dataset manifest and checksums. Windows and
Linux acceptance passed; all 73 required retrieval cases passed (110/116 overall).
The README reports the release's full counts and distinguishes the shared image
archive from the data embedded in these binaries. This publication does not
complete M10's image, observation, calibration, or resource-quality gates.

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
| M10 — Quality gates and compact releases | M4–M9 | In progress; reviewed benchmark frozen; JPEG parity and descriptive ranking improved (`09404cf`, `758401c`); image and specification quality still block release |

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
queue contains 508 candidate entities. Selected queries have now passed source/pixel
review; unselected candidates remain unreviewed. The expanded benchmark preserves
the frozen seed, and review coverage does not establish release quality.

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
and invalidate the appropriate cached build. The rebuilt gallery embeds 3,111 distinct
vectors across 3,122 records and 3,102 entities, up from 1,101 entities, using
33,554,327 preview bytes (under 32 MiB). Per-source covered entities are ArmyRecognition
11, Commons 148, Deagel 943, Fandom 11, MilitaryPeriscope 132, RadarTutorial 1,732,
RussianForces 7, VirtualGlobetrotting 100, and Wikipedia 18. All indexed records now
have completed description/OCR outcomes: 3,122 descriptions, 648 OCR observations,
and 2,474 empty OCR outcomes, with no failed or pending analyses. Shared image bytes
produce 3,756 unique observations and 3,780 chunks. A strict cached analysis rerun
made no fresh inference or network attempts. The full-bundle audit passes all 13
checks, including 9,442 source/text/research members unchanged from M9.

Full candidate dataset: `dabb846669424a7ea4b8b381d9011948f8ee6da185d60a528c9375b7df1164e7`.
Bundle SHA-256: `48fbe108afcfa2a3294e8f29707695d362f515186b86b20c41c9e1a57f0e7165`.
This complete gallery is for coverage/resource validation. The separate restricted
bundle below excludes benchmark query groups before calibration or evaluation.

Optional format-5 calibration now binds an acceptance rule to the exact retrieval
artifacts, mode, observation setting, and eligible entity pool. Python fits deterministic
thresholds from frozen reviewed development captures; Go applies the same integer
features before trimming results. Existing formats retain their responses. Thirteen
shared decision cases and framing/binding checks agree across languages. Packaging
preserves source and retrieval members, and publication checks reproduce thresholds
from hashed development evidence. No real thresholds have been fitted. Hashes/refitting
check evidence consistency; captured responses still require a trusted capture process.

Initial ignored review packets contained 360 text candidates, 406 visually inspected
archived images, and 344 inspected external negative-image candidates. Subsequent
source/variant review and the final allocation are recorded below. Duplicates,
components, illustrations outside the intended slices, and ambiguous variants remain
flagged. The user confirmed there is no existing reviewed image set to import.

Frozen-seed reruns remain pilot measurements: image ranks place eight of nine positive
queries first and combined queries two of two first, but accepted matches remain zero
because these modes abstain. All 63 research commands pass. Observation search finds
eight of ten within five globally and ten of ten with source filters, while abstaining.
M7 text has one filtered specification regression from rank five to six after captions
were added (17/18 within five, versus 18/18 before); global recall remains 18/18 and
the original 116-case baseline is unchanged. No held-out query was used to tune a fix.

Publication tooling now recomputes bound quality evidence and native resource samples
instead of trusting pass flags. It requires all five target reports plus the original
Windows hardware comparison, verifies exact executable/archive hashes, and defaults
manual workflow runs to validation only. Missing review/calibration evidence blocks
publication. Portable evidence staging retains the underlying report/query hashes.
The root README distinguishes the current text-only public release from newer local
multimodal builds. The calibration integration passes 931 Python tests, full lint/format
and type checks (including installed OCR/description extras), Go tests, and Go vet.
Nine default-bundle JSON responses match the prior implementation exactly.

The full M10 Windows candidate passes all 13 resource checks on the original Ryzen
5950X reference machine, using one first process and 20 subsequent fresh processes
per mode. Repeated JSON results are identical; filesystem caches were not cleared.

| Command | First / repeated p95 | Peak working set |
| --- | --- | --- |
| Default text | 1.108 / 1.145 s | 557.22 MiB |
| Image | 1.600 / 1.688 s | 384.70 MiB |
| Image + text | 2.590 / 2.677 s | 747.73 MiB |
| Text with observations | 2.210 / 2.233 s | 711.74 MiB |
| Text with manufacturer filter | 1.752 / 1.794 s | 628.22 MiB |

The single executable is 264.96 MiB; its ZIP is 242.11 MiB, within the 320/256 MiB
limits. Default and observation text also pass M1's relative latency/memory limits.
The public offline rebuild returns `changed: false`, reproduces the exact ZIP, encodes
only three text and three image probes, and makes no network attempts. Reports and
the local CLI are under ignored `build/m10/`. All 116 local baseline queries retain
their exact prior ranks and first results (73/73 required, 101/116 first, 110/116
within five). Native resource checks pass on Linux AMD64/ARM64, Windows AMD64, and
macOS ARM64. macOS Intel fails latency: image p95 7.67 s, combined p95 13.32 s,
observation text p95 6.57 s, and filtered text p95 4.00 s. Size, memory, and response
integrity pass there. A second native validation run passes all checks on the other
four targets and again fails Intel macOS latency, with image p95 8.76 s and combined
p95 8.84 s. Text-mode timings vary substantially between runs. The latency limits
remain unchanged. Local profiling attributes most image CPU time to generic
convolution; a compatible upstream SIMD experiment passes tests/probes but improves
local image timings only modestly. A narrow backend adapter now expresses eligible
single-batch 1×1 convolutions as matrix products, preserving the model and dependencies.
It covers 50 convolutions representing 92.8% of estimated convolution work. Thirteen
equivalence/fallback cases, the full Go suite, and frozen encoder probes pass. Local
interleaved diagnostics improve image latency by 19% and combined latency by 15%,
with unchanged rankings and a maximum score difference of 1.03e-7. The paired native
Intel macOS diagnostic also improves image median latency from 4.570 to 3.400 s and
combined latency from 6.843 to 4.664 s. All 96 responses retain their ranked IDs;
text responses match exactly, and both builds pass frozen verification.
Three samples per setting do not establish p95 compliance; the full native resource
gate remains pending. The temporary profiling workflow was removed after collecting
the evidence. General CI and all five encoder-parity targets pass for `04e6d89`.
General CI and all five native encoder-parity jobs for `ffbd1e0` pass. A fresh image export differed from the frozen
model by 20 half-precision values despite identical graph/checkpoint metadata. Native
parity now fetches the checksum-pinned model asset and verifies its exact bytes before
running Python/Go probes; the lock, exporter, and embedded model remain unchanged.
Actual download and cache reuse are checked. Linux's later report write exposed a
workflow directory-ownership issue, fixed by creating
the output directory before privileged network-isolated measurements.

Review readiness is recorded in `build/m10/review-readiness.json` and linked from
`build/m10/review.html`. The five positive packets now propose 25 development groups,
118 evaluation groups across 43 entities, and 46 independent gallery reserves. The
evaluation includes 98 ordinary photographs, 17 display photographs, and one each of
aerial, night, and panoramic photographs. Global copy/session unions identify 238
query records to exclude and 602 other same-entity records to quarantine. Cross-source
label review adds 12 equivalent IDs for six families, with ten additional gallery
records quarantined. Twenty reviewed crops inherit their parent evaluation groups.
The negative packets propose 35 development groups and 100 evaluation groups (50
in-domain and 50 unrelated), leaving 58 groups in reserve. All 135 selected negative
query wordings were reviewed against their source captions/titles. These negatives
cluster in 18 broad subject categories, limiting conclusions about general no-match
performance. Source pointers/hashes and archive/seed duplicate checks are recorded.
Text review now supplies 381 queries with 1,100 checked source citations: 20 development
and 100 evaluation cases per task plus 21 variant cases. Sixty evaluation queries
cover 33 conservatively defined confusable families. All query wordings are unique;
text families remain disjoint across development, evaluation, and the frozen seed.
Incorrect cross-reference aliases and homonyms were rejected; Dnestr/Dnepr/Hen House
remains entirely in development. Twenty reviewed diagram/photo pairs are available:
eight archived diagrams and twelve external source-backed drawings. Eight of twenty
external candidates remain held, including a plain silhouette outside the diagram
slice. Two transparent drawings exposed false near-duplicate matches because the
archive's perceptual hash ignores alpha; independent visual review records exact
hash-pair exceptions while retaining raw matches and every exact/URL/session link.
The CLI already composites transparency correctly. Two supplemental development
pairs now cover the ArmyRecognition and Wikipedia image filter pools, with independent
gallery groups and six additional query/session exclusions.
The final 1,114-case selection is now frozen before retrieval. Its gallery allowlist
contains 8,671 records: 62 reviewed representatives and 8,609 ordinary distractors.
It excludes 256 query/copy/session records and quarantines 769 other same-entity
records. All 62 representatives are indexed in the rebuilt gallery, which contains
3,117 vectors across 3,128 records and 33,554,233 preview bytes. Its 3,749 unique
observations form 3,768 chunks; all indexed records have completed outcomes, with no
failed analyses. A strict cached rerun made no fresh inference or network attempts.

Restricted format-4 dataset:
`bb2f75f815b853dccbbf6508455379c498261811491c8f095b8ebe34fd8d9640`.
Bundle SHA-256: `2fc41c766985fdc9f047998bb693eb9cd1af243c6c8bf3aa53a01d79a8a026e0`.
Frozen selection SHA-256: `d64d70d621445202f501adcea42a5984fda9d5d7928bead76dc133b3265bd81c`.
Development fixture SHA-256: `0f92d1881fdab015ef20907aa18d05f3f7850a774b9ab6c9855ee93d448c2b67`.
The fixture schedules 1,384 development captures across 27 exact mode/observation/
source pools, each with 35 independent negative groups. Reusing a negative in several
contexts does not add independent samples. All 1,384 actual CLI captures are complete
and validated. A command-rendering correction removed the redundant `--mode hybrid`
argument from combined queries; the frozen cases, query bytes, scopes, and executable
were unchanged. Capture SHA-256:
`1c5fd9cbf05631373bcfb9d903596c8535ef820f9f0c9c170a0ef08746362968`.

Development calibration fails closed: 22 of 27 profiles can fit, but five image-only
profiles cannot. Global image search ranks the correct entity first for 2/27 positive
photos and within five for 7/27. The other failures are ArmyRecognition (0/1 first,
1/1 within five), Commons (1/6, 4/6), MilitaryPeriscope (0/6, 2/6), and Deagel
(0/10, 2/10). A reviewed absent FAMAS query exceeds both the image cosine and margin
of every correct global first result. Accepting any of those positives necessarily
accepts that negative; one false acceptance in 35 gives a Wilson upper bound of
0.1453, above the frozen 0.10 limit. This is not a threshold-grid artifact. No
calibration artifact was produced and no expanded held-out evaluation has run.

Three independent pipeline controls pass: freshly encoded gallery images match their
stored vectors above cosine 0.99999998, exact-image searches retrieve the expected
entity first, and independent Python scoring reproduces Go's rankings. Different-view
misses persist in Python (expected ranks 18, 1,125, and 16). This supports image model
and scene sensitivity as the main explanation for those sampled failures. A separate
natural-JPEG decoding difference produces cosine 0.99862 on one development image,
below the existing 0.999 parity requirement; identical tensors infer identically.
Development-only work is checking decoder compatibility, contextual gallery images,
and model/preprocessing alternatives. Frozen inputs and M1 targets remain unchanged.
Reports are under `build/m10/acceptance/` and `build/m10/runtime/image-diagnosis/`.

Further development diagnostics retain the same candidates and labels. Preserving
the full query frame by padding or stretching does not improve retrieval: first/five
counts are 2/3 and 2/5, versus the original 2/7 out of 27. Precomputing paired CLIP
text features from catalog titles/categories also falls short: the best first count
is 3/27 and no tested text-anchor fusion improves recall within five. These experiments
remain ignored build artifacts and are not adopted. Three gallery association checks
find valid source links but contextual pictures: a distant T-72 behind lighter
vehicles, an APS-94 pod on a dominant OV-1 aircraft, and an APG-85 article illustrated
by an F-35 with the radar hidden. No query/winner photo-copy link was found. A general
visible-subject qualification would need source and image review; caption-name matching
alone is insufficient. Raw global combined ranking is also weak (5/25 first, 13/25
within five), so a feasible calibration profile must not be read as a quality pass.
The already-cached MobileCLIP2-B reference, tested against the same 3,117 gallery
images and 62 development queries, improves first/five counts to 7/10 out of 27.
Its float32 and float16 gallery controls agree. This is still far below M1's 80%/90%
photograph targets, so the larger model is not adopted or packaged. GPU/ONNX reference
probes agree within 1.17e-6; this experiment is about ranking, not release performance.

Text diagnostics separate the 60 development positives: exact designations are 20/20
first and within five, descriptions 14/20 and 20/20, specifications 1/20 and 5/20.
The name override promotes ordinary words such as “air” and “max” when they happen to
match entity aliases inside longer prose. Search policy v2 now reserves overriding
priority for exact names or explicit leading designations, preserving legacy behavior
and invalidating prior calibration bindings for new bundles. Its controlled candidate preserves every one of the frozen
bundle's 12,575 non-manifest members; only search-policy metadata and the resulting
recipe/dataset identities change. No acceptance fixture has been overwritten.

The final reviewed candidate (`758401c`) completes 201 fresh comparisons: descriptive
top-1 improves from 14/20 to 18/20, with 20/20 still within five. Exact designations
remain 20/20, specifications 1/20 first and 5/20 within five, and combined queries
5/25 and 13/25. All 116 baseline expected ranks are unchanged: 73/73 required pass,
101/116 first, 110/116 within five, MRR@20 0.9018162871611147. No comparison loses
expected rank. General CI, full Go tests, and vet pass after the final quote-parser
correction. The controlled binary SHA-256 is
`3eb5f94d73aa94206bd0ff1ba09291b8ade67068b0da9e24a4860acf488ca483`;
`build/m10/runtime/search-policy-v2/final/comparison.json` has SHA-256
`3bc2eaec15ac9f8b43dc345b009160795898ee42b3c297308de75530ac0997d5`.

Remaining specification errors are grounded in source evidence. Ariete ranks first
lexically but 1,082nd semantically; fusion places it 13th, while a competitor matches
the number 550 in horsepower although the query asks for range in kilometers. AN/FPN-36
has its frequency and power values split across overlapping chunks; no single chunk
contains the requested conjunction. Typed claims already retain the needed values.
`final/specification-gaps.json` records the exact source quotes, claims, chunks, and
rankings. These failures need property/unit-aware retrieval, not another name boost.

JPEG preprocessing v2 now uses Pillow-compatible chroma interpolation and RGB
rounding in pure Go. The three natural development JPEGs pass with cosine at least
0.999993; the formerly failing J-20 reaches 0.999994. All 30 local encoder probes
pass with unchanged tolerances. Synthetic JPEGs reproduce the old error and cover
subsampling, progressive encoding, odd/tiny dimensions, and edge handling. Legacy
v1 tensor hashes and vectors are unchanged on all three natural controls. Python
accepts either exact pinned recipe for existing bundles, while new builds use v2
and distinct cache/recipe identities. No weights, dependencies, CGO requirements,
or frozen artifacts changed. All five native targets pass the expanded 30-probe suite
with network restrictions verified (`09404cf`, run `35681482003`); general CI also
passes. Reports are under `build/m10/runtime/jpeg-native-v2/`. The integrated local
suite passes 961 Python tests, lint, formatting, types, full Go tests, and Go vet;
later added edge/malformed-version checks also pass focused tests.

Calibration validation now uses the actual indexed-image entity pool for image-only
queries, including source filters; text and combined modes retain their full eligible
pool. This matches Go's runtime scope calculation. Regression tests cover unindexed
entities and incorrect development scopes; 158 focused tests, lint, formatting, and
type checks pass. Independent combined-query photographs may reuse a generic text
constraint; image/group overlap remains prohibited and text-only duplicate protection
is unchanged. The expanded focused suite passes 159 tests.

- [ ] Improve specification retrieval using source-backed numeric/property evidence; verify unit/value matching on development queries.
- [ ] Improve image retrieval and qualify gallery subject evidence; current and larger-reference models remain below M1 targets on development photos.
- [ ] Run the frozen evaluation suite for text, image, combined, OCR, filters, and entity relationships; report results by task and source.
- [ ] Verify top-result accuracy, recall within the first five results, confusable variants, and no-match behavior meet M1's targets.
- [ ] Calibrate no-match decisions on larger development sets: M7 text negatives return candidates for 4/4 global and 3/4 filtered queries; image/generated modes currently abstain even for positive queries.
- [ ] Measure cold/warm latency, peak memory, and compressed executable size on all supported targets.
- [x] Bring text within the frozen reference-machine budgets: M10 default p95 1.145 s / 557.22 MiB, filtered p95 1.794 s, and observation p95 2.233 s / 711.74 MiB.
- [ ] Build and test one standalone executable per platform with required models, indices, evidence, and selected previews embedded.
- [x] Improve gallery coverage within the preview budget: development-only preview comparison and round-robin allocation increase entity coverage from 1,101 to 3,102.
- [ ] Validate deterministic dataset identities, cached rebuilds, checksums, source exports, and operation without network access.
- [x] Extend release change detection and tag identity to include image artifacts using the full deterministic dataset identity.
- [ ] Run lint, types, unit/integration tests, Python/Go parity, and native release acceptance checks.
- [x] Update the root README with CLI options/API behavior, capability availability, and the source table.
- [ ] Publish the verified binaries and checksums through the existing manual release process.

Complete when the released CLI satisfies the agreed quality and distribution budgets
and its results identify the supporting source and media evidence.
