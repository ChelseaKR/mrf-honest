# What a re-collection actually cost: the bytes behind a refresh, 2026-09-12

*Derived entirely from the committed assessment registries for 2026-08-19 and 2026-09-12. No
request was made to produce this document. Every figure below is re-derived by
`tests/test_published_claims.py` from those files, so it cannot drift from them.*

This exists because the cost of keeping grades current was being estimated rather than measured,
and the estimate was wrong in the direction that matters. A scheduled refresh is the thing a
dated grade needs most, and the argument for it has to rest on what a refresh really moves
across other organisations' servers.

## The headline

The 2026-09-12 re-collection moved **2,984,626,191 wire bytes** for 42 subjects. A cold
collection of the same 42 on 2026-08-19 moved **4,094,955,358**.

**Only 4 of the 42 files' bytes had changed.**

Conditional revalidation worked, and it was not enough. **22 of 42** subjects answered HTTP 304
and moved no body at all, covering 2,787,818,360 bytes of content the cache already held; those
same 22 subjects had cost 1,825,734,222 wire bytes on 2026-08-19. So the re-collection cost about
**62%** of what a cold pass of the same 42 would have cost that day (2,984,626,191 against
2,984,626,191 + 1,825,734,222 ≈ 4.81 GB) — not the order-of-magnitude saving a conditional-request
story implies.

## Where the 2.98 GB went

| Where it went | Subjects | Wire bytes | Share |
|---|---:|---:|---:|
| Bytes that genuinely changed | 4 | 1,048,458,605 | 35.1% |
| A first successful retrieval (HTTP 409 on 2026-08-19) | 1 | 630,969,424 | 21.1% |
| **Identical bytes, re-downloaded because the URL rotated its query-string credential** | 2 | **733,080,497** | **24.6%** |
| **Identical bytes, re-downloaded at the same URL** | 7 | **572,117,665** | **19.2%** |

**1,305,198,162 bytes — 43.7% of the whole re-collection — moved content this project already
held, byte for byte.** That is the number a refresh design has to attack, and it is not a
hospital's fault: it is what HTTP caching cannot see from where this tool stands.

## Why revalidation missed those nine files

The seven at the same URL split three ways, and only one of the three is addressable here:

- **No validator existed on either date** (3 subjects, 125,633,619 bytes). The server sent
  neither `ETag` nor `Last-Modified`, so there was nothing to make the request conditional with.
  A full download is the only correct behaviour.
- **A validator moved while the bytes did not** (2 subjects, 416,928,542 bytes). One server's
  `Last-Modified` advanced by 24 days and one server's `ETag` *and* `Last-Modified` both changed,
  and in each case the body hashed to exactly what was already cached. The server said it had
  changed; it had not. Nothing at this end can know that before downloading.
- **The validator was identical on both dates and a full body came back anyway** (2 subjects,
  29,555,504 bytes). Both of those `ETag`s end in `-gzip`. That suffix is what Apache's
  `mod_deflate` appends to a compressed response's entity tag, and it is a documented source of
  conditional requests that never match. **Stated as an observation, not a diagnosis:** the
  committed evidence records the two dates' validators and the byte counts, not the request and
  response headers of the run, so what the client sent and what the server did with it is not in
  hand. It is worth one targeted check against those two origins.

**The two at a rotated URL are the addressable case, and they are the largest single bucket.**
Three JSON subjects sit behind one Azure storage account which rotated the shared access
signature in its query string between the two collections. The verified cache is keyed on the
SHA-256 of the URL, so a rotated signature is a new key with no metadata and no validators — a
full download follows. But both files' `ETag` and `Last-Modified` came back **identical to the
values recorded against the old URL**:

| Subject | ETag, both dates | Last-Modified, both dates | Wire bytes moved |
|---|---|---|---:|
| HCA Florida Raulerson Hospital | `"0x8DEA0A005581ADE"` | Wed, 22 Apr 2026 18:50:27 GMT | 295,297,872 |
| Portsmouth Regional Hospital | `"0x8DED093CC7DA366"` | Mon, 22 Jun 2026 19:23:54 GMT | 437,782,625 |

The file's identity survived the URL change; only the credential moved. `mrf-honest probe`
already performs a bounded ranged GET (`Range: bytes=0-4095`, robots.txt first, no override), and
a response to it carries those headers. A refresh that read the validators at the new URL before
deciding to download — and compared them against every cached entry, not only the entry for that
exact URL — would have skipped both of these. That is **733,080,497 bytes, 24.6% of this
re-collection**, for two requests of at most 4 KiB each.

That is a proposal with a measured payoff, not a claim that it is built. It is not built.

## What this means for a scheduled refresh

- **A monthly refresh of this 42-file cohort costs roughly 3 GB** of other organisations'
  bandwidth to learn that four files changed.
- **Extrapolated to a complete census it is not cheap.** A cold pass over the 3,024-hospital
  frame was estimated at roughly 350 GB from this cohort's mean file size. At the ratio measured
  here, a monthly refresh of a fully collected census would move on the order of **200 GB**, not
  the ~25 GB that a "7% of files change, so 7% of the bytes move" reading would suggest. Those
  are different decisions.
- **The gap between 7% of files and 62% of bytes is the whole finding.** Change rates measured in
  files do not convert into bandwidth, because the files that get re-downloaded are not the files
  that changed, and because the largest files are over-represented among them.

None of this decides anything. The remaining gate on a scheduled job is the service/job tier
declaration recorded in [`docs/EXPANSION-PLAN.md`](../EXPANSION-PLAN.md) phase 14 as an owner
decision about what this project promises to keep running. This document exists so that the
decision is made against measured bytes.

## Reproducing it

```sh
python3 - <<'PY'
import json
def load(*paths):
    rows = {}
    for path in paths:
        for line in open(path):
            if line.strip():
                row = json.loads(line)
                subject = row["subject"]
                rows[f"{subject['publisher']['identifier']}/{subject['location_id']}"] = row
    return rows

old = load("data/cohorts/2026-08-19.assessments.jsonl", "data/cohorts/2026-08-19-csv.assessments.jsonl")
new = load("data/cohorts/2026-09-12.assessments.jsonl", "data/cohorts/2026-09-12-csv.assessments.jsonl")
for label, rows in (("2026-08-19", old), ("2026-09-12", new)):
    print(label, sum((r.get("retrieval") or {}).get("wire_size_bytes") or 0 for r in rows.values()))
PY
```
