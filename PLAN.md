# Entity search milestones

Help researchers and educators find static entities through names, descriptions,
specifications, and images, with results linked to captured evidence.

**Progress: 10/10 milestones complete under the research-search-v1 release scope.**
The original automatic-identification targets remain unmet and are reported
separately. Completion means a tested offline research tool with measured limits.

| Milestone | Delivered | Status |
| --- | --- | --- |
| M1 — Baseline and acceptance targets | Frozen cases, baseline measurements, quality and resource contracts | Complete |
| M2 — Shared media evidence and archive | Content-addressed originals, source associations, download outcomes | Complete |
| M3 — Two-source media pilot | End-to-end acquisition, archive, and evidence validation | Complete |
| M4 — Portable image encoder | Pinned image model embedded in the Go executable | Complete |
| M5 — Image and combined queries | Local JPEG/PNG queries, filters, ranked evidence-linked suggestions | Complete |
| M6 — OCR and visual descriptions | Cached analysis, generated-text provenance, opt-in retrieval | Complete |
| M7 — Text and combined ranking | Names, source captions, semantic ranking, numeric specifications | Complete |
| M8 — Relationships and precise filters | Parsed claims, field predicates, comparisons, sourced relationships | Complete |
| M9 — Coverage across all sources | Shared media pipeline and explicit coverage/exclusions for all 12 sources | Complete |
| M10 — Quality gates and compact releases | Reproducible dataset, frozen evaluation, five native targets, published binaries and image archives | Complete |

## M10 scope and evidence

The release follows [research-search-v1](tests/fixtures/research_release.json).
The original [identification contract](tests/fixtures/search_acceptance.json) is
unchanged. Development experiments with MobileCLIP2-S0, MobileCLIP2-B,
DINOv2-small, and SigLIP2-B did not establish reliable equipment identification.
The compact S0 model remains selected; the frozen held-out queries were not used
to tune retrieval. Image, combined, and generated-text searches return
`no_supported_match` and `uncalibrated` alongside suggestions.

- [x] Preserve the reviewed fixture and query-disjoint gallery; execute all 1,794
  held-out query/scope runs and validate every returned contribution.
- [x] Report raw ranking separately from accepted identification, with source/task
  breakdowns and confidence intervals. Preserve four rejected runs caused by one
  malformed JPEG; do not silently exclude or repair frozen query data.
- [x] Preserve all 73 required text cases and the 116-case baseline report.
- [x] Analyze all 9,370 selected originals with OCR and visual descriptions;
  replay cached analysis without running either analyzer again.
- [x] Write the complete dataset to two independent output paths with networking
  disabled and verify identical SHA-256 hashes.
- [x] Validate all captured source content and original-image archive hashes;
  compare every Cambridge Pixel CLI source/HTML/Markdown export with its archive.
- [x] Pass native functionality, encoder parity, text regression, and resource
  checks on Windows amd64, Linux amd64/arm64, and macOS amd64/arm64 with networking
  blocked. Each platform archive contains exactly one executable.
- [x] Publish the latest CLI and complete saved-original image dataset; verify
  every uploaded asset's size and SHA-256 before publishing.
- [x] Update README installation, CLI/API behavior, counts, and limitations.

The frozen 4,337-entity evaluation gallery differs from the larger production
corpus. Global raw top-1/top-5 results on positive queries were:

| Query | Positive cases | Top 1 | Top 5 |
| --- | ---: | ---: | ---: |
| Text | 321 | 298 (92.8%) | 314 (97.8%) |
| Independent image | 158 | 12 (7.6%) | 24 (15.2%) |
| Image and text | 118 | 29 (24.6%) | 58 (49.2%) |

Visual results are similarity suggestions; accepted visual identifications remain
zero. Text search returned candidates for 99/100 global negative queries; reliable
no-match detection is not established. The fixed text baseline retains recall@5
of 110/116 and MRR 0.9018162871611147. Half-precision vector storage preserved all
201 controlled first-result/expected ranks. Previews are at most 160 pixels on the long edge; image
vectors and generated analysis use the originals.

## Published archive

[Verified CLI and image dataset](https://github.com/osint-builders/pipelines/releases/tag/cli-9a10b95015b17b50e70a4fe5c341c002fe7be866761d3b3e4d774930545b510e)
— dataset `9a10b95015b17b50e70a4fe5c341c002fe7be866761d3b3e4d774930545b510e`, source commit `8baaa3945c5e71275a9c17683d9018813b7c0b32`.
Native validation: [GitHub Actions](https://github.com/osint-builders/pipelines/actions/runs/35869423833).

The release embeds 8,463 entities, 9,595 evidence pages, 269,028 source facts,
9,370 image vectors, and previews covering 7,596 entities. It includes
11,968 generated records in 12,000 searchable chunks.
Each executable works without an external runtime, model download, or data file.
Compressed binaries range from 373.8–388.7 MiB; all native resource checks
pass the published contract.

Supplemental archives contain 21,751 distinct original files (5.48 GB), associated
with 21,882 saved source image URLs. Coverage accounting retains 68 failed image
URLs and all exclusions; no source downloads are pending. Saved originals are
complete, while unavailable or unsupported source URLs remain explicit gaps.

Cambridge Pixel contains all 393 current entries, including every supplied HTML
row: 379 have individually reviewed image associations, including 31 ambiguous
family/configuration matches; 14 remain unresolved. All 368 selected URLs are
saved. The image index covers 376 entries; three GIF originals remain in the
downloadable archive. All 772 evidence pages pass CLI export comparisons.

The release includes `quality.json`, `quality-evidence.zip`, the research contract,
five native validation reports, image manifests, and `SHA256SUMS`. Quality evidence
preserves the original failed identification checks, frozen benchmark artifacts,
source audits, generated-description spot checks, and offline rebuild hashes.
Generated observations can contain errors and remain separate from source facts.
