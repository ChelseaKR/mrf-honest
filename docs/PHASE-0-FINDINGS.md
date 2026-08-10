# Phase 0 findings: the constraint is real

The phase-0 survey was measured 2026-08-05 from `davis-ca/residential`; the final hardened-reader
acceptance was remeasured 2026-08-09. Every number here was observed, not estimated.

**Verdict: proceed.** The stop condition was "if hospital files turn out to be uniformly small and
clean, rescope or drop." They are neither.

## 1. Hospitals are discoverable by convention, and this inverts the fhir-scorecard problem

CMS requires a `cms-hpt.txt` at the root of the public website selected to host the MRF. That
origin may belong to a hospital, health system, or vendor; it must not be guessed from a hospital's
corporate domain. Each location entry has five attributes, and one document may repeat the block
for multiple locations:

The contact fields in this example are illustrative; they are not claimed as values observed from
Stanford's published TXT evidence.

```
location-name: Stanford Health Care
source-page-url: https://stanfordhealthcare.org/for-patients-visitors/price-transparency.html
mrf-url: https://stanfordhealthcare.org/content/dam/SHC/...
contact-name: Price Transparency Team
contact-email: transparency@example.org
```

Four of five domains tried returned a usable one on the first attempt.

This is the **opposite** of the payer FHIR situation in `fhir-scorecard`, where only 7 of 9
organizations with documented base URLs could be verified and 15 guessed URLs resolved to nothing,
forcing a registry curated one developer portal at a time. Here the registry can be **built
automatically once the selected MRF-hosting origins are confirmed**, which changes the shape of
the whole project: each structured document can yield one or more location records without
guessing an MRF path. Establishing the hosting origin is still a provenance step; an absent file
on an arbitrary corporate domain is not evidence about the hospital's publication.

MRF filenames also follow a CMS convention: `{EIN}_{hospital-name}_standardcharges.{json|csv}`,
which gives a second structured signal (the EIN identifies the filer).

**Already observed target-probe failure mode:** `mayoclinic.org` returned **403** to an identified
client. That corporate-domain probe was not confirmed as Mayo's selected MRF-hosting origin or
publication path, so it is not a hospital finding. It does demonstrate why the assessment must
retain failed targets and provenance rather than silently dropping them.

## 2. File sizes: not small

| Hospital | Size | Type |
|---|---:|---|
| Stanford Health Care | 154,579,203 B (155 MB) | `application/json` |
| University of Cincinnati Medical Center | 64,828,148 B (65 MB) | `application/json` |
| OHSU | 13,498,398 B (13.5 MB) | `application/zip` |
| Cedars-Sinai | no `content-length` (chunked) | streamed |

Cedars-Sinai serving without a `content-length` is itself worth noting: you cannot know the size
before committing to the download, which matters for any polite fetch budget.

## 3. Memory amplification is the actual constraint

Naive `json.load()` on the 65 MB Cincinnati file:

```
parse time : 0.4 s
peak RSS   : 506 MB   (7.8x the file size)
```

Speed is not the problem; **memory is**. At 7.8x, Stanford's 155 MB file implies roughly 1.2 GB
resident, and payer files run one to three orders of magnitude larger than these. A streaming
reader is not a nicety, and this number is the justification.

## 4. The first file I touched was already broken

`json.load()` failed outright on Cincinnati's file:

```
JSONDecodeError: Unexpected UTF-8 BOM (decode using utf-8-sig)
```

A UTF-8 byte-order mark made the byte stream unreadable by the standard parser. Strict JSON
producers should not emit a BOM even though parsers may choose to tolerate one. This is a practical
hardening case, and it appeared on file number one without looking for it.

## 5. The thesis is confirmed by the data itself

CMS's payer schema (`CMSgov/price-transparency-guide`, source-checked 2026-08-09) defines
`negotiated_type` as an enum:

```
negotiated | derived | fee schedule | percentage | per diem
```

and `negotiated_rate` as a bare `number` for all five. **A value of `85` is $85 or 85% depending
on a sibling field.**

The hospital side shows the same hazard in live data. Across 30,114 charge items in one hospital
file:

| Methodology | Count |
|---|---:|
| fee schedule | 192,778 |
| other | 48,736 |
| percent of total billed charges | 5,909 |

Nearly 6,000 percentage-based rates sit in the same array as 190,000+ dollar amounts. Any
aggregation that does not segment by methodology produces a confidently wrong number. That is the
error this project is built to refuse, and it is the default outcome of naive analysis.

## What this changes in the plan

- **Start with hospitals, not payers.** Discoverable, standardized, thousands of publishers, and
  large enough to force real engineering. Payers come later, when streaming is proven.
- **Discovery is automatable from confirmed hosting origins.** Build a multi-entry `cms-hpt.txt`
  discovery module early; it is the cheapest path to a genuinely large registry and the thing
  `fhir-scorecard` could never have. Never infer a missing publication from an unconfirmed
  corporate-domain probe.
- **Encoding tolerance is a first-class requirement**, not a bug fix. BOM handling, encoding
  detection, and zip containers all appeared in a sample of four.
- **Segment by methodology from the very first model.** Retrofitting that later would mean every
  published number before the fix was wrong.


---

## Phase 1 result: streaming, measured against the same file

The streaming reader was built on these findings and measured on the same 65 MB Cincinnati file:

| Approach | Peak RSS | Ratio to file |
|---|---:|---:|
| naive `json.load` | 506 MB | 7.8x |
| first streaming reader (historical) | 27 MB | 0.42x |
| final hardened reader | **33,865,728 B (32.30 MiB)** | **0.5224x** |

The final reader parsed 30,114 charge items with zero parser problems and handled the BOM rather
than failing. Maximum RSS remains below the input size instead of several multiples above it,
which is the bounded-memory property the pipeline needs.

### The bug worth recording

The first working version corrupted **exactly one item per buffer refill**, and every test passed.
The cause: `_scan_value` captured absolute `start` and `end` indices into the buffer, and a refill
between those two captures compacted the buffer underneath them, so the slice mixed a stale start
with a fresh end. It was invisible on small fixtures because no refill ever occurred, and invisible
on large ones because the items still parsed as valid JSON, just the wrong ones.

Two fixes, and the second is the real one:

1. Pin the buffer against compaction for the duration of a single value scan, which bounds growth
   by one item rather than by the file.
2. **Change the API so the bug cannot be expressed.** `_scan_value` now returns the value's bytes
   instead of a span. Nothing outside the function ever holds an index into a buffer that can move.

The regression test forces a 512-byte chunk size rather than relying on a large fixture, because
the defect only appears at boundaries and a realistic fixture would hide it.

### 2026-08-09 remeasurement after parser hardening

`stream.py` changed after the original result: it gained strict comma/trailing-content checks,
bounded exact problem accounting, invalid-UTF-8 evidence, and streaming discard of large sibling
values. The current reader was therefore remeasured rather than carrying the 27 MB figure forward
unchanged:

```sh
/usr/bin/time -l .venv/bin/python -c \
  'from pathlib import Path; from mrf_honest.stream import stream_array_items; p=Path("data/cache/uchealth.json"); f=p.open("rb"); n=sum(1 for _ in stream_array_items(f,"standard_charge_information")); f.close(); print(n)'
```

The final result on macOS with Python 3.14.5 was 30,114 items, zero parser problems, 9.25 seconds
real time, and 33,865,728 bytes maximum RSS (32.30 MiB, 0.5224 times the 64,828,148-byte input).
macOS also reported a 26,231,240-byte peak memory footprint. RSS and peak footprint are separate
operating-system measurements and are retained as reported. The earlier 27 MB result remains the
historical phase-1 measurement; no causal claim is made for the difference because both the parser
and measurement environment changed.
