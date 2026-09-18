# What one origin actually cost, end to end, 2026-09-13

*One real collection, on a laptop, against one health system's own `cms-hpt.txt`. Every figure
below is re-derived from the committed evidence by `tests/test_origin_run.py`, so it cannot
drift: byte counts and dispositions from
[`data/origins/2026-09-13-chihealth-com.assessments.jsonl`](../../data/origins/2026-09-13-chihealth-com.assessments.jsonl),
wall clock, pacing, robots and process memory from
[`data/origins/2026-09-13-chihealth-com.cost.json`](../../data/origins/2026-09-13-chihealth-com.cost.json),
which records only what an assessment row cannot carry.*

*The letters below are file-level presentation grades for one dated retrieval, not a ranking of
hospitals or a determination of compliance with 45 CFR part 180. What a letter does and does not mean is in
[how-we-compare.md](../how-we-compare.md), how each dimension is assessed is in
[how-we-grade.md](../how-we-grade.md), and how to dispute a letter or have a row removed is in
[CORRECTIONS.md](../CORRECTIONS.md).*

Everything the project knew about the cost of collection came from operator-invoked runs over
*sampled* targets: 42 files drawn from 40-odd unrelated origins. Nothing had ever collected one
origin's whole publication set in one pass, which is the shape any origin-scoped request would
take. This is that run.

## The headline

**Seventeen locations, fifteen files, 96,766,261 wire bytes, 7 minutes 10 seconds, 77 MB of
RSS, nothing near the size ceiling, and every one of the seventeen returned a letter.**

| | |
|---|---:|
| Locations the origin's `cms-hpt.txt` listed | 17 |
| Distinct files those locations point at | **15** |
| Locations that returned a letter | **17 of 17** |
| Locations not assessed | **0** |
| Wire bytes, whole collection | **96,766,261** (92.3 MiB) |
| Decoded bytes | 2,471,413,408 (2.30 GiB) |
| Wire : decoded | **1 : 25.5** |
| Wall clock, collection | **430.2 s** |
| Wall clock, `cms-hpt.txt` + 15-probe pre-pass | 28.3 s, plus 835 wire bytes for the document itself |
| Peak RSS, whole process | **76,955,648 bytes** |
| Largest file, decoded | 227,005,895 bytes, against a ceiling of 1,073,741,824 |
| `too_large` | **0** |
| Grades | **A, all 17** |

## Choosing the origin cost nothing, and it was the whole game

The `census` verb already knew, at zero new requests, that 49 retrieved `cms-hpt.txt` documents
name 656 locations and 550 distinct files, 406 of them in an implemented profile. What nobody had
done was sort the origins by **what collecting one would cost somebody else**.

The selector that matters is not the one the project had been using. Published cost figures are
*decoded* bytes — a mean of 182 MB per file. What lands on a third party's bandwidth bill is
`wire_size_bytes`. Over the two committed 2026-09-12 cohorts, per host:

| Host | decoded | wire |
|---|---:|---:|
| `www.cedars-sinai.org` | 884.0 MB | 23.7 MB |
| `healthy.kaiserpermanente.org` | 215.5 MB | 6.4 MB |
| `www.chihealth.com` | 138.5 MB | 5.8 MB |
| `www.uchealth.com` | 65.3 MB | 2.3 MB |
| `dam.upmc.com`, `apps.scripps.org`, `sthpiprd.blob.core.windows.net`, HCA's Azure account | 199–632 MB | **identical to decoded** |

**An origin that serves `Content-Encoding: gzip` costs 20–40× less of somebody else's bandwidth
for the same graded file.** Of the 29 hosts that returned a body to the 2026-09-12 cohorts,
**10 gzip and 19 do not** — so this is a property of about a third of the registry, and it is
knowable from evidence already held, for every origin, before any order is priced. Sorting the
candidate origins that way, before spending a byte:

| Origin | locations in an implemented profile | predicted wire |
|---|---:|---:|
| **chihealth.com** | 17 | **~99 MB** |
| commonspirit.org | 72 | ~160 MB |
| healthy.kaiserpermanente.org | 41 | ~262 MB |
| wvumedicine.org | 20 | ~1.6 GB |
| christushealth.org | 35 | ~7.4 GB |
| upmc.com | 38 | ~7.6 GB |
| hcafloridahealthcare.com | 114 | ~11–23 GB |

The prediction for chihealth.com was ~99 MB. **The measurement was 96,766,261 bytes.**

Four origins were excluded on grounds that are not cost:

- **`northside.com`** (5 locations) answered HTTP 403 from `assets.northside.com` on both
  2026-08-19 and 2026-09-12. Under the grading rule in force when this run was planned,
  collecting it would have published **five** F letters on named hospitals from one client's
  403 — the defect in [#99](https://github.com/ChelseaKR/mrf-honest/issues/99), multiplied by
  five. It was not collected, and no origin whose file host has a recorded HTTP barrier was.
  (#99 was fixed later the same day by
  [#120](https://github.com/ChelseaKR/mrf-honest/pull/120): a retrieval failure now carries a
  letter only from two or more recorded attempts, and below that its finding is published with
  the letter withheld as `NOT_GRADED`.)
- **`centura.org`** (20 locations) is `robots.txt`-disallowed at `csdam.widen.net`: 20 requests
  for 20 `NOT_GRADED` rows and no letters.
- **`texashealth.org`** and **`hackensackmeridianhealth.org`** each have a measured `too_large`
  at the same host.

And the 114-location origin, the one the product design names as the stress case, was rejected
deliberately: it serves identity-encoded from Azure blob storage at 295–631 MB per measured file,
so collecting it would move 11–23 GB of one company's bandwidth to answer a question a 17-file
run answers for 0.1 GB.

## Four things the run measured that nobody had

### 1. Seventeen listed locations are not seventeen files

Two pairs of locations in this document name the **same** `mrf-url`:

| One file | Listed for |
|---|---|
| `470379755-1336184019_good-samaritan-hospital_standardcharges.json` | CHI Health Good Samaritan; CHI Health Richard Young Behavioral Health |
| `470484764-1508941097_alegent-health-bergan-mercy-health-system_standardcharges.json` | CHI Health Creighton University Medical Center – Bergan Mercy; CHI Health Creighton University Medical Center – University Campus |

`mrf-honest systems` reports both, and reports that each file's envelope `location_name` list
**covers the listed locations** — so this is a legitimate publication shape, not a defect. But it
means a 17-location order is a 15-file order, and a price or a promise stated per location is
stating something the deliverable does not have. **An order must be de-duplicated on `mrf_url`
before anything is fetched.** This run deliberately was not, so that the cost of not doing it
could be measured:

- **9,905,200 wire bytes and 67.1 seconds**, 10.2% of the collection's bandwidth and 15.6% of its
  wall clock, spent retrieving two files this project already held.

### 2. Conditional revalidation does not work at this origin, and now there is proof

`docs/findings/what-a-re-collection-actually-cost-2026-09-12.md` recorded that two subjects whose
`ETag` was identical on both dates were nonetheless sent a full body, noted that both of those
ETags end in `-gzip` — what Apache's `mod_deflate` appends to a compressed response's entity tag —
and said in terms: *"what the client sent and what the server did with it is not in hand. It is
worth one targeted check against those two origins."*

This is that check. The first two observations cost nothing, because the two duplicate URLs
above make the experiment happen inside a single run; the third was one deliberate extra request,
made to rule out the possibility that a validator written seconds earlier was somehow special.
Observed three times on 2026-09-13:

| # | When | Validator held | Server answered |
|---|---|---|---|
| 1 | seconds after the first fetch of the same URL | `"640506b-653ea923a2240-gzip"` + `Last-Modified` | **HTTP 200, 4,074,346 wire bytes** |
| 2 | seconds after the first fetch of the same URL | `"842e8ff-653ea86a9edc0-gzip"` + `Last-Modified` | **HTTP 200, 5,830,854 wire bytes** |
| 3 | a separate `mrf-honest fetch`, two minutes later | `"5600c0a-653ea7219a580-gzip"` + `Last-Modified` | **HTTP 200, 3,720,478 wire bytes** |

That the conditional headers were sent is not inferred from the outcome. `fetch_url` loads the
cache metadata, verifies the blob, and only then builds the request; for all three URLs the
metadata was present and the blob verified, and `_conditional_headers` returns both
`If-None-Match` and `If-Modified-Since` for each.

Every one of this origin's ETags carries the `-gzip` suffix, including the one on `cms-hpt.txt`
itself, and RFC 9110 has a recipient ignore `If-Modified-Since` when `If-None-Match` is present —
so an entity tag the server will not match takes the `Last-Modified` down with it rather than
falling back to it. **That is the mechanism this evidence is consistent with, and it is not what
was measured.** What was measured is narrower and is enough: a correct conditional request for a
body that had not changed returned the whole body, three times, at one origin, on one date.

**This is the most expensive finding here for the product.** A monthly refresh of these 17
locations would move the full 96.8 MB again to learn that nothing changed. Whatever a refresh
costs at this origin, it is not "7% of a cold pass". (A later measurement at ten more origins,
[cross-origin-conditional-revalidation-2026-09-14.md](cross-origin-conditional-revalidation-2026-09-14.md),
found `chihealth.com` to be the outlier rather than the rule.)

### 3. `probe` cannot price an order here

The pre-purchase page in the product design promises a buyer their own numbers before payment,
and `probe` — one ranged GET of 4,096 bytes — is the primitive that is supposed to answer
"how big is this?" without downloading it.

All 15 probes succeeded and all 15 correctly identified JSON. **All 15 came back HTTP 200 rather
than 206, and none carried a `Content-Length`**, so `declared_size` was `null` for every one.
The server ignores `Range` on this path and frames the response without a length.

Two consequences, both honest ones:

- A pre-purchase page at this origin can state the location count, the file count and the format.
  **It cannot state a byte budget**, and must not invent one.
- A probe against a server that ignores `Range` *starts* a full transfer that the client then
  abandons after 4 KiB. The bytes that reach the wire before the close are whatever was in
  flight, which is not 4 KiB and is not measurable from this end. The `probes` block of the cost
  record says so rather than recording a number it does not have.

### 4. The per-host interval never binds, and memory is not a constraint at all

Every one of the 17 retrievals recorded `waited_seconds: 0.0`, reason `no_wait`. The minimum
interval is 2.0 s and the fastest retrieval took 12.5 s, so **the transfer itself is the pacing**
at this file size; the interval only binds on cheap requests, as it did across the 15-probe
pre-pass (28.3 s for 15 probes). This origin's `robots.txt` was retrieved and applied before
every request, allowed each one, and declares no `Crawl-delay`.

Peak RSS for the whole process — 17 streamed downloads, 17 gzip decodes, 17 complete inspections
of files up to 227 MB — was **76,955,648 bytes**. The disk high-water mark is the cache, 2.3 GiB,
and only because this run retained every body; a fulfillment path that deleted each body after
writing its record would peak at the largest single file.

## What this says about the thing it was run to test

The product design's §5.4 names one measurement as the one that could still kill the product:
whether the wall clock and the byte cost of an on-demand order are survivable. For this order:

| Design's estimate, for a 25-location order | Measured, for a 17-location order |
|---|---|
| ~2.4 GB of wire, i.e. ~96 MB per location | **96,766,261 bytes for 17 locations — 5.69 MB per location, 17× cheaper**, because this origin gzips |
| 15–60 minutes, "with a tail that is somebody else's server" | **7 minutes 10 seconds** |
| pacing >= 50 s, and minutes if a `Crawl-delay` is declared | **0.0 s of waiting** on every one of the 17; the transfers dominate and no `Crawl-delay` is declared |
| memory an open question | **76,955,648 bytes**, not a question |

**The promise in the design — "normally the same day, always within two business days" — is
comfortable here, and the design's own per-location byte estimate is 17× too pessimistic for a
gzip-serving origin and roughly right for one that is not.** The spread between origins is larger than the
spread the design contemplates, so the honest form of the promise is per-origin, and the census
already knows which kind each origin is before anyone pays.

What is *not* settled: none of this was observed on a GitHub-hosted runner, the two ends of the
distribution (a 72-location origin and a 114-location identity-encoded one) are still unmeasured,
and this run did nothing about issue [#99](https://github.com/ChelseaKR/mrf-honest/issues/99) — it
stayed away from the origins that would have multiplied it, which is a choice about what to
collect and not a fix. The fix landed separately in
[#120](https://github.com/ChelseaKR/mrf-honest/pull/120). The comparison committed here is derived
under that rule (comparison version 5): each of the 17 letters records `retrieval_attempts: 1`,
and the two-attempt floor does not apply to any of them, because every one rests on a body that
arrived, was hashed and was read to the end.

## What this run did not do, deliberately

- **It did not enter `data/cohorts/`.** The cohorts published there are seeded probability samples
  against `data/frames/2026-08-19.frame.json`, with Wilson-score interval estimates. This run is
  a complete enumeration of one origin chosen on cost, so folding it in would change what those
  cohorts' estimates are estimates of, and — because the site renders the newest comparison of
  each profile — would have replaced the published 17-subject JSON sample with this one. It lives
  in `data/origins/`, is not rendered, and its own statistics block refuses with
  `no_sampling_frame` rather than computing a share of a population it did not sample.
- **It did not raise concurrency, retry around a refusal, or replay a stored URL.** Every one of
  the 17 URLs is the one this origin's own `cms-hpt.txt` published on the day of the run.
- **It did not grade anything it did not read.** All 17 rows carry a verified body, a
  `content_sha256`, and `scan_completed: true`.

## Where coverage now stands

| | |
|---|---:|
| Files **graded** in committed evidence | **55** distinct URLs (was 41) |
| Files **located** by discovery | **550** |
| Rows in the CMS sampling frame | **3,024** |

**55 of 550 located, and 550 of 3,024 frame rows.** The first ratio moved because 14 new files
were read; the second did not move at all, because re-retrieving this origin's `cms-hpt.txt`
returned the same 17 locations and the same 15 files the registry already held. They are two
different denominators and neither is the other's percentage.

Those two counts come from `mrf-honest census` over the **local** discovery registry, which is
gitignored (`data/registry*.jsonl`) because it carries publisher contact details — the same
constraint `what-discovery-already-located-2026-09-12.md` records. They are therefore *not* among
the figures `tests/test_origin_run.py` re-derives; a checkout without a registry gets an empty
census rather than a wrong one. Everything else in this document is gated.

And one number that had never been zero before. For **chi-health**, `mrf-honest systems` reports
17 of 17 listed locations served by a file graded here and **0 not assessed in this cohort** —
the bucket `systems.py` calls "this project's sampling scope, not a defect", which until now has
held almost every location of every system in the registry.

## Reproducing it

```sh
# The byte counts, the dispositions and the grades, from committed evidence, no requests:
python -m mrf_honest compare \
  --assessments data/origins/2026-09-13-chihealth-com.assessments.jsonl \
  --manifest data/origins/2026-09-13-chihealth-com.json \
  --generated-at "$(python -c "import json;print(json.load(open('data/origins/2026-09-13-chihealth-com.comparison.json'))['generated_at'])")" \
  --format json | cmp - data/origins/2026-09-13-chihealth-com.comparison.json

# The reconciliation against the origin's own document:
python -m mrf_honest systems \
  --assessments data/origins/2026-09-13-chihealth-com.assessments.jsonl \
  --discovery <a registry containing the 2026-09-13 chihealth.com discovery record>

# Every figure in this document:
python -m pytest tests/test_origin_run.py -q
```
