# What stands behind a published letter, 2026-09-13

*Derived entirely from the five committed comparison documents under `data/cohorts/`. No request
was made to produce this document. Every figure below is re-derived by
`tests/test_published_claims.py` from those files, so it cannot drift from them.*

This project publishes one letter under a real hospital's name. Until this change, nothing on the
page, in the dataset, or in the receipt said how much observation that letter rested on — and for
five of them the answer was **one request**.

## The two numbers

Counted over every row in the five committed comparison documents. A letter is "attributed to a
failed retrieval" when it was derived without a verified body: the grade is a statement that the
file could not be obtained, and the only evidence for it is the requests that were made.

| | Before (`origin/master`) | After |
|---|---:|---:|
| Rows published | 90 | 90 |
| **Letters published** | **82** | **77** |
| Letters derived from a verified body | 77 | 77 |
| **Letters attributed to a failed retrieval** | **5** | **0** |
| …of those, resting on 2 or more recorded attempts | **0** | **0** |
| `NOT_GRADED` with the reason stated | 8 | 13 |

Restricted to the two cohorts the published site actually renders (2026-09-12, both profiles):
**38 letters published, 2 attributed to a failed retrieval, 0 of those 2 resting on more than one
attempt.** After: **36 letters published, 0 attributed to a failed retrieval.**

The second row of that table is the finding. Not one of the five strongest sentences this project
had published about a named organization had been observed more than once.

## Why the count was one, and why nobody chose that

`fetch._RETRYABLE_HTTP_STATUSES` is `{408, 425, 429, 500, 502, 503, 504}`. It is a
fetcher-efficiency list, and a good one: a server answering 403 is not going to change its mind
because it is asked again immediately, and hammering it would be rude. 401, 403, 404 and 409 are
therefore recorded after exactly one request.

Nothing downstream ever asked how many requests there had been. `grade_assessment` read the
retrievability status, saw `FINDINGS`, and returned `F`. So the evidential standard for the
strongest claim this project makes about a hospital was being set, as a side effect, by a list
whose purpose was to avoid being impolite to servers. No one decided it; it fell out of the
layering.

That is the whole of defect [#99](https://github.com/ChelseaKR/mrf-honest/issues/99).

## The rule, and why it is asymmetric

**A successful retrieval needs one observation. A failed one needs more than one.**

That is not a hedge; the two are not the same kind of evidence.

- When the bytes arrive, the claim is *these bytes*, and the digest is published beside the
  letter. One observation carries it completely, and anyone can check it against the file.
- When the bytes do not arrive, the claim is *about the server*, and a single request from a
  single client on a single date is not distinguishable from trouble at this end — a WAF that
  refuses one datacentre's address range, a geo-restricted CDN edge, a transient origin swap.

The module already made exactly this call, and said so, for a certificate that would not verify:

> "From one attempt that is not distinguishable from this machine's trust store missing a root,
> so it is not attributed to the publisher."

And the README already stated the principle in general terms — "a certificate that will not
verify, a `robots.txt` that says no, and this project's own size ceiling are all **not graded**,
because from one attempt none of them is distinguishable from a problem on this end." An HTTP
barrier was the one access refusal charged to the publisher on one observation, and nothing in
the code said why it was different.

`cohort.RETRIEVAL_FAILURE_MINIMUM_ATTEMPTS = 2` now holds it to the same standard.

## What is withheld, and what is not

Nothing is hidden. The finding is still published, with its code, its severity, its HTTP status,
its date, its citations and its attempt count. The row is still a row. What is withheld is the
**letter**, which becomes `NOT_GRADED` with a reason that says all of it:

> not graded: the identified download attempt did not produce a verified file, and 1 attempt from
> one client on one date is a fact about this request rather than about the publisher; this policy
> attributes a retrieval failure only from 2 or more recorded attempts: …

This can only ever *withhold* a letter. Raising the floor cannot mint one, cannot change a letter
derived from a body that arrived, and cannot move a grade in the direction of a publisher's
disadvantage. It is a one-way valve, and a test holds it to that.

## The `F` did not become unreachable

A rule whose failing state cannot be reached is a gate that cannot fail, and this repository has
shipped one of those before (#95). It is worth being explicit that this is not another.

`F` from a retrieval failure is still reachable **today, with the fetcher exactly as it is**,
because retryable statuses exhaust `FetchPolicy.retries` before they are recorded: a network
error, a truncated transfer, a 429 or a 503 that never clears all arrive at
`grade_assessment` with `attempts = 3`. Those are attributed, and they are the cases where a
second and third observation genuinely were made. Two committed tests drive both sides of the
rule from records that differ only in their attempt count.

What is *not* reachable is an attributed letter for 401, 403, 404 or 409, because the fetcher
does not ask twice. Whether it should — one confirming request, paced by the same per-host
interval as any other, costing one round trip and no body — is a change to the retrieval
procedure, and therefore to `retrieval_policy_fingerprint`, which is validated field-by-field
inside every committed assessment record. It is a decision about what this project promises, not
a refactor, and it is left to the maintainer. Until it is taken, these statuses are published as
dated observations and not as letters, which is the conservative direction.

## Every letter now names its own evidence

`files[].grade.retrieval_attempts` is published in the comparison document
(`COMPARISON_VERSION` 5) and rendered on each file page as *"Retrieval attempts behind this
grade"*. It does not yet reach `dataset.csv` or the per-file receipt, which select their grade
fields explicitly; carrying it there is a schema change for each and is left for its own
change rather than claimed here. An unrecorded count says so, and is never
rendered as `1`: "we did not record how many times we asked" and "we asked once" are different
facts.

## What this costs, and what it buys

It costs five letters: the project now publishes 77 letters where it published 82, and each of
the five withdrawn ones carries a sentence saying what was observed, when, how many times, and
why that is not enough to put under a hospital's name.

It buys the only thing that matters for a document anyone would rely on: **no sentence this
project publishes under a hospital's name rests on a single unconfirmed request.**
