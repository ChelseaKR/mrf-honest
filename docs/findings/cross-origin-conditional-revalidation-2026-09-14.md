# Whether a cheap refresh generalises: conditional revalidation across a wider sample of origins

*An engineering finding about how the real hospital-hosted origins this project's cohorts grade
respond to conditional GET, measured 2026-09-14 against 12 real origins drawn from the committed
2026-08-19 and 2026-09-12 cohorts. It is not a ranking of any hospital and not a statement about
care.*

## Why this exists

[PR #113](https://github.com/ChelseaKR/mrf-honest/pull/113) (`what happened when the tool graded
one origin end to end`) found that a whole-origin collection at `chihealth.com` sent
`If-None-Match` and `If-Modified-Since` against a validator it had just verified, three times, and
got HTTP 200 with the full body back all three times — "a monthly refresh here costs a cold
pass." That was one origin, three observations of the same origin, and its own "what is still
unmeasured" section said so: *"None of this was observed on a GitHub-hosted runner... whether the
revalidation behaviour generalises"* was left open. A portfolio-wide monetization review read that
single result as the reason a recurring refresh SKU might not be priceable at gtfs's $49/mo
(`monetization-ranking-2026-09-13.md` §3.3). Generalising a per-origin cost from one origin is
exactly the error the same review's own §2.4 the-cheap-refresh-premise correction names elsewhere
in the portfolio — a number measured on unrelated subjects does not price a thing sold per
subject. This measures more subjects.

## Method

Twelve real MRF file URLs, drawn from the project's own committed
`data/cohorts/2026-08-19*.assessments.jsonl` and `data/cohorts/2026-09-12*.assessments.jsonl`
(the same origins this project has already, legitimately, retrieved through its own
robots.txt-respecting collector — no new or unvetted path was probed), chosen to span the
Content-Encoding split the 2026-09-12 finding named (10 of 29 measured hosts serve gzip, 19 do
not): 6 gzip-serving hosts, 6 plain hosts.

For each origin:

1. **`HEAD`** the URL (no body transferred either way) and record `ETag`, `Last-Modified`,
   `Content-Length`, `Content-Encoding`.
2. Wait, then send a real **conditional `GET`** — `If-None-Match` and/or `If-Modified-Since` set
   from the validator just observed — and record the status code.

`HEAD` is used to capture the validator rather than a full `GET`, specifically to avoid a second
full-body download per origin: several of these files are hundreds of megabytes to nearly a
gigabyte, and RFC 9110 §9.3.2 defines conditional-request evaluation identically for `GET` and
`HEAD`. This was cross-checked directly: for `www.msh.ms.gov`, a full `GET` (76,646 bytes
downloaded) followed by a conditional `GET` using the `ETag` the full `GET` itself returned gave
the same result (304) as the `HEAD`-primed version. The conditional `GET` in step 2 was capped at
20 MB (`curl --max-filesize`) so that an origin answering 200 instead of 304 could not turn this
measurement into an uncapped download of another organisation's bandwidth; the cap stops the
transfer after the status line and headers are already in hand, which is all this measurement
reads. One host (`www.grmedcenter.com`, 113 MB) and one (`mindenmedicalcenter.com`, 960 MB) would
have exceeded it had they answered 200; both answered 304, so the cap was never exercised in
practice.

Identifying User-Agent on every request, one request pair per origin, several seconds between the
two requests and between origins — the same courtesy `docs/findings/truncated-transfer-attribution-2026-08-18.md`
used for its own single-`HEAD` cross-origin measurement.

## Result: 9 of 10 measurable origins revalidate for free

| Origin | Encoding | Validator sent | Conditional `GET` result |
|---|---|---|---|
| www.msh.ms.gov | gzip | ETag + Last-Modified | **304** |
| www.cmhc.org | gzip | ETag + Last-Modified | **304** |
| www.commonspirit.org | gzip | ETag + Last-Modified | **304** |
| secure.claraprice.net | gzip | ETag | **304** |
| estimator.myinsightcare.com | gzip | ETag + Last-Modified | **304** |
| healthy.kaiserpermanente.org | gzip | Last-Modified only | **200** (full body) |
| msc.rochesterregional.org | plain | ETag + Last-Modified | **304** |
| www.stelizabeth.com | plain | Last-Modified only | **304** |
| www.grmedcenter.com | plain | Last-Modified only | **304** |
| mindenmedicalcenter.com | plain | Last-Modified only | **304** |
| www.frederickhealth.org | plain | — | **not measurable** (see below) |
| hospitalpricedisclosure.com | plain | — | **not measurable** (see below) |

**9 of the 10 origins where a conditional request could be made at all answered 304 and moved no
body.** Combined with PR #113's three observations at `chihealth.com` (0 of 3 answered 304), the
running total across the two efforts is **9 of 11 distinct origins (82%) support cheap
revalidation; 2 of 11 (18%) always return the full body regardless of a matching validator.** The
encoding split does not explain the difference: 5 of 6 gzip origins revalidate for free here, and
so do all 4 of 4 plain origins — `chihealth.com`'s own `ETag` carries the `mod_deflate`
`-gzip` suffix the 2026-09-12 finding flagged as a documented source of conditional-request
mismatches, but three of today's gzip origins (`www.msh.ms.gov`, `www.cmhc.org`,
`www.commonspirit.org`) revalidated cleanly with plain, non-`-gzip`-suffixed `ETag`s, and a fourth
(`secure.claraprice.net`) revalidated with a weak `W/` `ETag`. **The evidence does not support
"gzip origins are the ones that cost a cold pass" as a rule**; it supports "most origins, gzip or
not, are cheap, and a minority are not, for reasons specific to that origin."

### The one origin that never revalidates, and why it looks different from `chihealth.com`

`healthy.kaiserpermanente.org` sends no `ETag` at all, only `Last-Modified` — and that
`Last-Modified` value was observed to differ between two requests made minutes apart on the same
day (`Sat, 12 Sep 2026 09:45:43 GMT` in an earlier probe, `Sat, 12 Sep 2026 06:25:48 GMT` in the
timed pair reported above), despite being sent back as the *exact string this script had just
received* in its own `If-Modified-Since` header. That is not a validator the origin is failing to
honour; it is a header that does not describe a stable fact about the file, so no conditional
request could ever match it. This is a different failure mode from `chihealth.com`, where PR #113
sent a validator the server itself had issued and verified matched the cached blob, and still got
200 three times — there the header *was* stable and the server still ignored it. Two different
origins, two different reasons, the same operational consequence: a refresh against either one is
always a cold pass.

### Two origins were not measurable at all, and that is itself evidence

- **`www.frederickhealth.org`** answered its `robots.txt` and the file's own `HEAD` with a
  Cloudflare managed challenge (`HTTP 403`, `cf_chl_opt`) to an identifying, non-browser
  User-Agent. No validator could be obtained because no request of any kind got past the
  challenge. This is the same class of finding [#99](https://github.com/ChelseaKR/mrf-honest/issues/99)
  was about — an automated client refused outright — at a different origin, and it means a
  recurring refresh of this origin cannot be built as a conditional `GET` at all; it would need to
  clear a bot-management challenge on every run, or it does not run.
- **`hospitalpricedisclosure.com`**: the exact `mrf_url` this project's own 2026-08-19 and
  2026-09-12 cohorts recorded as `fetched` now redirects (`301` → `302`) to
  `https://hospitalpricedisclosure.com/error/default.htm`. The file the committed grade cites has
  moved or been withdrawn since the last collection. This is a different failure from a cold
  revalidation — a refresh here would not merely re-download the same bytes, it would need
  re-discovery before it could retrieve anything, which is a cost `probe` and conditional `GET`
  both assume away.

## What this changes about the recurring-refresh question

The 2026-09-13 monetization ranking's single falsifying measurement for the product's recurring
half was: *"Repeat PR #113's conditional-revalidation observation at 4–5 more origins spanning
the encoding split... If most behave like the one measured, the monthly refresh is a cold pass and
the recurring SKU cannot be priced from gtfs's $49."* Measured at 10 more origins (6 gzip, 4
plain, exceeding the 4–5 asked for): **most do not behave like the one measured.** 9 of 10
revalidate for free; `chihealth.com` is the outlier this sample found, not the rule. A monthly
refresh across a cohort shaped like this sample would move the full body for roughly one origin in
five to six, not for all of them — closer to the 62%-of-cold-cost figure
`what-a-re-collection-actually-cost-2026-09-12.md` already measured for *file*-level change
(a different question — whether the bytes changed — but a similar order of magnitude) than to
"every refresh is a cold pass."

This does not price the recurring SKU by itself. It removes the one measured reason to believe the
recurring half is structurally unpriceable, and it surfaces two costs a pricing model would still
need to account for: an origin that never revalidates (here, 1 of 11) still needs to be re-fetched
in full every cycle, and an origin whose listed file URL has moved (here, 1 of 12 sampled) needs
re-discovery, not just a conditional request, before a refresh can run at all.

## What is still open

- **n=11 distinct origins across two efforts, against 49 located origins and a 3,024-hospital
  frame.** This narrows the question PR #113 left fully open; it does not close it. A production
  refresh design would want this measured continuously (each cycle is a free additional
  observation) rather than as a one-time sample.
- **Both unmeasurable origins here happen to be `plain`, not `gzip`.** Whether an automation
  barrier or a rotated/withdrawn URL correlates with encoding, origin size, or anything else
  useful for triage is not measured by a sample this size.
- **This measurement did not go through `mrf-honest`'s own `Politeness`-gated fetcher** — it used
  a one-off script with an identifying User-Agent and manual pacing, the same shortcut
  `truncated-transfer-attribution-2026-08-18.md` took for its own cross-origin `HEAD` measurement.
  A scheduled refresh job would want this behaviour built into `probe` or `fetch` itself (record
  `ETag`/`Last-Modified` on every fetch, already true; compare against the previous cycle's
  recorded validator before deciding to download, not yet built) rather than repeated as an
  external check.

## Reproducing it

There is no committed corpus this re-derives from — every number above came from live requests
made on 2026-09-14 and cannot be replayed offline. The twelve URLs are the `retrieval.url` values
already committed at `data/cohorts/2026-08-19{,-csv}.assessments.jsonl` and
`data/cohorts/2026-09-12{,-csv}.assessments.jsonl` for the hosts named above; re-running the same
`HEAD` then conditional-`GET` pair against each will not reproduce the same status codes forever,
because a hospital's server configuration, and the files themselves, can change.
