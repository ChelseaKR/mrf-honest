# Phase 0 findings: the constraint is real

Measured 2026-08-05 from `davis-ca/residential`. Every number here was observed, not estimated.

**Verdict: proceed.** The stop condition was "if hospital files turn out to be uniformly small and
clean, rescope or drop." They are neither.

## 1. Hospitals are discoverable by convention, and this inverts the fhir-scorecard problem

CMS requires hospitals to publish `cms-hpt.txt` at their domain root. It is a structured file:

```
location-name: Stanford Health Care
source-page-url: https://stanfordhealthcare.org/for-patients-visitors/price-transparency.html
mrf-url: https://stanfordhealthcare.org/content/dam/SHC/...
```

Four of five domains tried returned a usable one on the first attempt.

This is the **opposite** of the payer FHIR situation in `fhir-scorecard`, where only 7 of 9
organizations with documented base URLs could be verified and 15 guessed URLs resolved to nothing,
forcing a registry curated one developer portal at a time. Here the registry can be **built
automatically from a list of hospital domains**, which changes the shape of the whole project:
coverage becomes an engineering problem rather than a manual research problem.

MRF filenames also follow a CMS convention: `{EIN}_{hospital-name}_standardcharges.{json|csv|xlsx}`,
which gives a second structured signal (the EIN identifies the filer).

**Already observed failure mode:** `mayoclinic.org` returned **403** to an identified client. Bot
protection versus a publication requirement is a real tension and belongs in the grading as a
finding, not as a silent gap.

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

A UTF-8 byte-order mark makes a technically-JSON document unreadable by a standard parser. This is
exactly the "technically conforming, practically unusable" category the project exists to grade,
and it appeared on file number one without looking for it.

## 5. The thesis is confirmed by the data itself

CMS's payer schema (`CMSgov/price-transparency-guide`, updated 2026-07-30) defines
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
- **Discovery is automatable.** Build a `cms-hpt.txt` discovery module early; it is the cheapest
  path to a genuinely large registry and the thing `fhir-scorecard` could never have.
- **Encoding tolerance is a first-class requirement**, not a bug fix. BOM handling, encoding
  detection, and zip containers all appeared in a sample of four.
- **Segment by methodology from the very first model.** Retrofitting that later would mean every
  published number before the fix was wrong.
