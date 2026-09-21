# Entity search milestones

Help researchers and educators find static entities quickly using names, descriptions,
specifications, and images, with every result linked to captured evidence.

**Progress:** 0/10 milestones complete. **Next:** M1.

Existing foundation: 11 source adapters, shared crawl/extract/audit commands, archived
evidence, offline text search, and standalone CLI releases.

Keep this file as the current tracker. Update statuses and checkboxes as work lands.
Mark a milestone complete only when its acceptance checks pass; record the validation
artifact or commit in its status cell. Store generated reports under ignored build/data
directories. Keep the root README focused on shipped CLI commands and the source table.

| Milestone | Depends on | Status |
| --- | --- | --- |
| M1 — Baseline and acceptance targets | Existing CLI | Planned |
| M2 — Shared media evidence and archive | M1 | Planned |
| M3 — Two-source media pilot | M2 | Planned |
| M4 — Portable image encoder | M1, M3 | Planned |
| M5 — Image and combined queries | M2, M3, M4 | Planned |
| M6 — OCR and visual descriptions | M3, M5 | Planned |
| M7 — Better text and combined ranking | M1, M5, M6 | Planned |
| M8 — Entity relationships and precise filters | M1, M7 | Planned |
| M9 — Media coverage across all sources | M3, M5, M6 | Planned |
| M10 — Quality gates and compact releases | M4–M9 | Planned |

M2 and the runtime feasibility work in M4 can proceed together after M1. Final model
selection uses the pilot images from M3. M8 and M9 can proceed independently once
their dependencies are complete. Prioritize media capture from expiring sessions.

## M1 — Baseline and acceptance targets

- [ ] Record current text retrieval quality, cold/warm latency, peak memory, and compressed release sizes.
- [ ] Define representative tasks: exact designation, descriptive query, specifications, photograph, and photograph plus text.
- [ ] Build evaluation sets covering unseen views, crops, diagrams, similar variants, and queries with no matching entity.
- [ ] Separate development and evaluation images by underlying photograph, including resized and cropped duplicates.
- [ ] Fix numerical acceptance targets for retrieval quality, false matches, latency, memory, and binary size before model selection.
- [ ] Define the first release's query modes, result fields, media retention, and compatibility requirements.

Complete when a repeatable baseline and explicit acceptance targets exist. Record target
hardware and distinguish measured results from proposed budgets.

## M2 — Shared media evidence and archive

- [ ] Add versioned media records containing stable IDs, original URL, evidence/entity associations, captions, dimensions, MIME type, capture time, and content hash.
- [ ] Let adapters describe media through one shared interface; keep website-specific discovery and supporting files in their source folders.
- [ ] Implement one resumable downloader with retries, atomic temporary files, response validation, and source-scoped authentication and redirects.
- [ ] Keep verified originals in a durable local archive; use temporary storage for decoding, resizing, and other intermediate work.
- [ ] Deduplicate identical bytes across sources and track near-duplicate images without automatically merging entity identities.
- [ ] Version processing recipes and cache derived output by media hash, model revision, and preprocessing settings.
- [ ] Extend shared commands and audits to report discovered, saved, excluded, failed, and unassociated media. Keep indexing and packaging offline.

Complete when interrupted downloads resume, source associations survive deduplication,
existing text snapshots remain usable, and extraction can replay saved media offline.

## M3 — Two-source media pilot

- [ ] Capture Military Periscope images from structured content blocks, preserving captions and section/subject associations.
- [ ] Capture Wikimedia Commons media from file records, resolving original files and available previews.
- [ ] Exclude navigation graphics and unrelated illustrations; represent ambiguous or multiple depicted entities explicitly.
- [ ] Publish media manifests and coverage reports for both sources through the shared pipeline.
- [ ] Select representative pilot images for retrieval evaluation and inspect their entity associations.

Complete when both sources use the same media workflow and every discovered candidate
has a recorded outcome. Failed downloads remain visible and resumable.

## M4 — Portable image encoder

- [ ] Benchmark compact candidates, beginning with MobileCLIP2-S0, against a retrieval-quality reference model.
- [ ] Prove the selected exported model works in the CLI's pure-Go runtime before committing to it.
- [ ] Match Python and Go image preprocessing and embeddings using fixed image probes, including orientation, color, resize, crop, and normalization.
- [ ] Compare full-precision and smaller model/vector representations against M1's quality and resource budgets.
- [ ] Pin model files, hashes, dimensions, preprocessing, and output normalization in the build manifest.
- [ ] Decide whether direct text-to-image search warrants shipping a paired text encoder; image-only encoding supports the initial image-query path.

Complete when the selected model passes parity and offline execution checks on every
supported platform and architecture. Report measured size, memory, and latency.

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
| militaryperiscope | Pilot pending | — |
| commons | Pilot pending | — |
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
