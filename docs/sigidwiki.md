# SigIDWiki signal references

The `sigidwiki` source imports the complete identified-signals table at
<https://www.sigidwiki.com/wiki/Database>. Each distinct linked signal becomes a
`signal` entity. The captured table supplies its description, reported frequency
span, reception modes, modulation, bandwidth, location label, status, audio links,
and waterfall example. Linked articles remain references; their full contents,
audio files, and raw I/Q recordings are outside this adapter's capture scope.

Each entity retains its own table row and rendered evidence. `get --format source`
exports the exact captured database response; `get --format html` and
`get --format markdown` export the selected signal's row. The signal's article URL
is preserved separately from the captured database URL. Glossary tooltips are
removed from search text so their definitions do not contaminate descriptions or
numeric fields. Identical duplicate rows collapse; conflicting duplicates fail.

## Use

These commands require a CLI built with the new source; the previously published
12-source release predates this addition.

```sh
pipelines search --source sigidwiki "over the horizon radar"
pipelines search --source sigidwiki "Automatic Identification System"
pipelines list --source sigidwiki --where "modulation=FMCW" --where "bandwidth<=50 kHz"
pipelines list --kind signal --where "signal_status=Active"
pipelines search --source sigidwiki --image waterfall.png
```

Use a returned `sigidwiki:ID` with `get`, `facts`, `media`, or `compare`.
Image search returns uncalibrated similarity suggestions. It does not identify a
transmitter or decode a recording.

| Field | Meaning |
| --- | --- |
| `frequency_range` | Reported frequency span, normalized to Hz |
| `bandwidth` | Reported bandwidth or bandwidth interval, normalized to Hz |
| `modulation` | One source-listed modulation per claim |
| `reception_mode` | One source-listed receiver mode per claim |
| `signal_location` | Source location label, including labels such as Worldwide |
| `signal_status` | Active, Inactive, or Unknown or intermittent, from the table legend |

Frequency spans do not enumerate occupied channels. For example, a reported
161.975–162.025 MHz span does not satisfy `frequency_range=162 MHz`.
The existing strict interval semantics apply: repeated `>=` and `<=` predicates
select records whose complete reported span falls within the requested bounds.
These records do not assert a `frequency` channel claim. Location labels do not
become manufacturer, operator, or transmitter-location claims.

## Images and capture

Only images in each record's waterfall cell are associated with that signal.
MediaWiki thumbnail paths resolve to their original image URLs. The shared
`NoWaterfallFiller.png` placeholder is explicitly excluded. If profiles share an
actual sample image, their associations are marked ambiguous. Saved originals
receive checksums and source references; supported gallery images receive the
existing offline embeddings and JPEG previews.

An already captured placeholder remains in the immutable media archive for
provenance, but has no eligible entity associations or searchable preview.
GIF originals are retained in the image export; the current image encoder
supports the captured JPEG and PNG samples.

The adapter uses a ten-second request interval and one media download worker.
Interrupted captures resume through the normal archive and media pipeline.
Source terms and attribution are retained without assigning an inferred license.

```sh
pipeline-build crawl sigidwiki --root /path/to/reference-data
pipeline-build media sigidwiki --root /path/to/reference-data --download
pipeline-build audit sigidwiki --root /path/to/reference-data
```

Include `--source sigidwiki` alongside the other sources when packaging. Use the
existing image-model, observation, CLI-build, and image-export steps to produce
the complete offline distribution. The research extension is now
`entity-research-v2`; the CLI and bundle validator also accept the original v1
field catalog.

For an incremental gallery build, `--image-selection PATH` can retain the previous
release's indexed image families and add the new source's samples. Pair it with
`--include-unselected-captions` to retain all source captions in text search.
Without that flag, an explicit selection also restricts caption search, which
remains useful for isolated evaluations. The preview budget is 40 MiB; the
10,000-vector limit is unchanged.

The focused retrieval cases are in `tests/fixtures/sigidwiki-retrieval.json`.
Run them against a built binary with
`python tools/evaluate_cli.py PATH_TO_BINARY --cases tests/fixtures/sigidwiki-retrieval.json`.
They check named-reference retrieval, not recognition of unseen signal recordings.

## Captured dataset

The September 23, 2026 capture adds 598 distinct signals (one identical duplicate
table row was collapsed). The combined dataset has 9,061 entities across 13
sources. The source provides 579 sample-image URLs for 581 signals; 17 profiles
use its missing-waterfall placeholder. Six samples are GIFs. The JPEG/PNG samples
produce 572 distinct searchable image vectors, with duplicate bytes stored once.

The local distribution is in `dist/sigidwiki/`: the Windows executable,
`dataset.zip`, manifests, original-image archives, retained gallery selection,
verification reports, and `SHA256SUMS`. These generated files stay outside Git.
The capture and preservation checks are recorded in
[`sources/sigidwiki-capture.json`](sources/sigidwiki-capture.json).

The gallery retains all 9,370 previously indexed image vectors and adds the
572 signal samples. Existing entities, image associations, previews, and source
captions are checked against the prior complete release. The previous public
download remains unchanged until a new release is published.
