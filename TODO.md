# Project status and remaining work

Status checked September 20, 2026 UTC. [README.md](README.md) remains the setup,
architecture, source-methodology, release, and CLI reference. This file is the handoff
checklist: completed work, observed failures, and outstanding work.

## What worked

- [x] Standalone Python producer and Go consumer CLI, separate from application code.
- [x] Explicit crawls only, with throttling, robots enforcement, saved original responses,
  resumable archives, and publication guards that preserve the previous snapshot on failure.
- [x] Offline extraction into stable source-qualified entities, full Markdown, original
  HTML/JSON, attribution, and evidence-backed facts. Multiple sources share this pipeline.
- [x] Fully offline query embeddings, hybrid/vector search, exact cosine nearest neighbors,
  `similar`, source/kind/category filters, and ID-based full-content exports.
- [x] One binary embeds the model, dataset, vectors, and evidence. No API key, first-run
  download, Python installation, or writable consumer cache is needed.
- [x] Content-based release gating: unchanged source content preserves the dataset ID and
  skips publication. Scraping and release dispatch remain explicit operations.
- [x] Ten source adapters implemented, crawled, extracted, evaluated, and included in the
  current local bundle:

| Source | Entities | Verified method and scope |
| --- | ---: | --- |
| Radartutorial | 1,735 | English equipment discovery and HTML detail extraction |
| Deagel Armies | 1,285 | Browser discovery across all four statuses; 797 family pages; separate variants |
| VirtualGlobetrotting | 100 | Rolling radar-site RSS and linked detail pages; retain earlier feed members on refresh |
| RussianForces | 57 | Full Atom articles and reviewed entity identities; 15 articles |
| Wikipedia | 41 | Chinese military-radar category traversal and full article evidence |
| Wikimedia Commons | 151 | Russian radar equipment/site categories and associated media descriptions |
| Army Recognition | 11 | Air Defense Radars category 139; both legacy and current detail layouts |
| Fandom Military Wiki | 46 | Public MediaWiki API; 53 articles grouped into reviewed identities |
| ClimateViewer | 383 | Historical GeoJSON Point records with exact original collection export |
| Cambridge Pixel | 385 | Visible catalog rows cross-checked against embedded ProductModel records |

The current bundle contains **4,194 entities, 4,926 evidence pages, and 15,272 vectors**.
Dataset ID: `00b3d0cad1a8c2b508af65b2e2fdb879dc08eefefb0339a7fecb8fff8cb00998`.

## Verification that passed

- [x] 153 Python tests; rerun for this handoff.
- [x] Go tests with `NODOWNLOAD`; rerun for this handoff.
- [x] Python lint, formatting, type checks, package build, and Go vet during the last
  source implementation. This handoff changes documentation only.
- [x] All 63 required CLI retrieval cases passed in the latest 98-case evaluation.
  Of 35 optional semantic diagnostics, 29 passed and six missed their thresholds.
- [x] Unfiltered `russian cheeseboard` returned Radartutorial's 96L6E first in both
  hybrid and vector modes on Windows and Linux amd64.
- [x] Windows and Linux amd64 bundle/export acceptance; Linux also ran without networking
  and with a read-only filesystem. The current Windows binary passed `verify` again
  during the CMANO evaluation, including four Python/Go embedding-parity probes.
- [x] All five platform binaries built locally. Linux arm64 verification passed under
  emulation. macOS binaries were cross-compiled; native release acceptance is still pending.
- [x] Saved-source offline replay and unchanged repackaging checks passed. The latest
  repackaging reported `changed: false`.

These are measured checks against the captured corpus, not a claim of general search
accuracy or verified real-world equipment performance.

## What did not work, or has limited coverage

| Area | Observed limitation and current handling |
| --- | --- |
| CMANO DB | Direct HTTP returned a Cloudflare challenge; agent-browser required human verification; the in-app browser reported a 522 origin timeout. No adapter, records, or embeddings were added. |
| CMANO listing links | The indexed-page renderer duplicated relative path segments. Some resulting pages contained only navigation; they cannot substitute for original entity pages. |
| VirtualGlobetrotting completeness | RSS exposes 100 entries and KML only 25; the observed category had 497 entries. Robots excludes pagination/search paths, so this is not a complete category scrape. |
| RussianForces completeness | Initial coverage is the 15-post rolling feed, not the full historical archive. Previously captured evidence is retained on subsequent refreshes. |
| Fandom ordinary HTML | Article/category HTML and robots.txt returned challenges. The independently accessible public API worked without authentication. Robots rules were unavailable, not confirmed permissive. |
| Army Recognition scope | The supplied fighter URL with category parameters resolves to Air Defense Radars. The implementation covers those 11 entities, not all aircraft or the entire site. New pagination needs review because robots excludes `?start=`. |
| Cambridge Pixel filters | Legacy selection/Apply left all 385 rows displayed during browser evaluation. The complete initial table and matching JSON-LD worked and determine membership. |
| ClimateViewer freshness | Historical map data does not establish current deployment. Unassigned LineString range overlays are retained in the original response, not attached to sites as verified performance. |
| Additional content | Images, PDFs, external references, and other attachments remain links. Media download/OCR, cross-source entity merging, and geographic radius search are not implemented. |

The six optional misses below are from the latest source-filtered, pure-vector CLI
evaluation. Every case targets a top-five result; the evaluator retrieves twenty.

| Source | Query | Expected entity rank |
| --- | --- | --- |
| Deagel | passive artillery locator using sound and infrared sensors | Outside top 20 |
| VirtualGlobetrotting | radar inside a protective dome on Apple Orchard Mountain | 20 |
| RussianForces | satellite with NORAD identifier 68826 | 13 |
| Wikipedia | Type 1478 airborne fire control radar | 11 |
| Commons | radar to detect stealth aircraft cruise missiles and unmanned aerial vehicles | Outside top 20 |
| Commons | Repeynik radar | 7 |

## Remaining work

- [ ] **Publish the first distributable CLI release.** No GitHub releases existed when
  checked for this handoff. The release workflow and local binaries exist; an actual
  five-platform publication has not been exercised end to end. Follow the README's
  staging and manual dispatch procedure after selecting releasable source data.
- [ ] **Finish source redistribution review.** Confirm the allowed distribution scope
  and attribution for each included source. Army Recognition and Cambridge Pixel do not
  provide an open redistribution license in the captured material; ClimateViewer's
  captured notice has a noncommercial restriction. Scraping success is not permission
  to publish the combined dataset. Choose a permitted subset if necessary.
- [ ] **Complete native platform acceptance in the release workflow.** In particular,
  both macOS targets still need native bundle verification, search, and export checks.
  Require every platform job to pass before making a release public.
- [ ] **Back up producer data independently of Git.** Archives, snapshots, the model,
  and the verified bundle currently live locally. Preserve them in suitable durable
  storage so a new machine can reproduce releases without depending on this workstation.
- [ ] **Resume CMANO evaluation when access works or an authorized original export is
  available.** Verify robots policy, six-category completeness, canonical URLs, and
  representative detail pages first. Then implement and test the adapter, preserve
  simulation/database-version provenance and variant IDs, crawl, replay offline, compare
  embedding representations, and run CLI search/export checks. Its proposed architecture
  and exact blockers are in the README. Do not register a placeholder as a working source.
- [ ] **Improve the six semantic misses.** Compare entity-specific descriptions, explicit
  source aliases, and retrieval representations against the committed cases. Preserve
  full evidence and all required regressions; keep optional misses visible until fixed.
- [ ] **Expand evaluation coverage as sources grow.** Add independent natural-language
  queries and ambiguous names across sources, beyond the current small diagnostic sets.
- [ ] **Decide whether to expand bounded sources.** Historical RussianForces coverage,
  the remainder of VirtualGlobetrotting, additional Army Recognition categories, and
  a possible CMANO Cold War database each need an explicit scope and permitted discovery
  method. The current counts should not be presented as whole-site coverage.

Cross-source resolution, media/OCR, and geographic search are possible future features,
not prerequisites for the current entity-search CLI. New websites should reuse the
existing adapter protocol and include original-response fixtures, offline replay,
identity checks, and retrieval/export evaluations.

## Where the data and build outputs are

These are the current workstation paths. They are intentionally excluded from Git;
source code, source fixtures, evaluation cases, workflows, README, and this checklist
are versioned. Pushing code to `main` does not publish the dataset or CLI binaries.

| Artifact | Current location |
| --- | --- |
| Source archives and published snapshots | `C:/Users/erikz/.hai/reference-data/SOURCE/` |
| Exact captures and crawl manifest | `SOURCE/archives/CRAWL_ID/html/` and `manifest.sqlite` under that data root |
| Extracted entities and Markdown | `SOURCE/published/snapshots/SNAPSHOT_ID/` under that data root |
| Current snapshot selector | `SOURCE/published/current.json` under that data root |
| Embedding model | `build/model/` in this repository |
| Reusable embedding cache | `build/entity-vector-cache/` |
| Verified combined bundle | `build/dataset.zip` |
| Binaries, manifest, and checksums | `dist/` |
| Detailed latest retrieval report | `build/cambridgepixel-evaluation/cli-retrieval.json` |
| CMANO access evaluation captures | `build/cmano-evaluation/` |

The SQLite files are producer crawl bookkeeping. Consumer lookup and vector search use
the catalog and vector matrix embedded in the binary, not a separately installed database.
