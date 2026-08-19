# A web page served where a file was requested was published as a hospital's unreadable file

*An engineering finding about this project, observed 2026-08-19. It describes this tool's
behaviour and the sentences it publishes. It is not a ranking of any hospital, not a statement
about care, and not a legal compliance determination.*

## The rule this is an instance of

**A fetch that succeeded is not evidence that the document arrived.**

HTTP status describes the *transfer*, not the *payload*. A 200 means a server chose to answer;
it does not mean it answered with the thing that was asked for. Every stage downstream of a
retrieval that treats "the fetch returned" as "the document is here" will eventually describe
something that is not the document — and, in a tool that publishes beside a real organisation's
name, will describe it *as that organisation's*.

The fix has two halves, and the order matters:

1. **Record what actually arrived**, not merely that something did. The response carries
   statements the server made about its own payload — the declared media type, the declared
   length, the status. Discarding them destroys the only evidence that could later separate
   "this file is broken" from "this was never the file".
2. **Let the downstream stage refuse, rather than interpret.** The stage that has both the
   arrival evidence *and* the parse result is the one that can say which event happened. It
   should state what it observed and decline to go further — never reconstruct a likely story
   from the symptom alone.

The sibling instance of this rule, in `femtech-privacy-eval`, is written up in that repository's
ADR 0003. Both tools had the same shape of defect and needed different second halves, which is
itself the useful part; see [What did not transfer](#what-did-not-transfer) below.

## The defect

[The 2026-08-18 finding](truncated-transfer-attribution-2026-08-18.md) closed the case where a
download stopped early. It left its sibling open, and named it precisely:

> An HTTP 200 that returns an HTML landing page instead of the document is still described by
> the same "could not be streamed to completion" sentence as a genuinely malformed JSON file.
> [...] `Content-Type` is not recorded on the assessment at all today.

Measured on the composition path on 2026-08-19, before this change: an HTTP 200 returning a
`text/html` landing page and an HTTP 200 returning a truncated JSON document produced grades
whose reasons were byte-identical strings:

> the standard_charge_information array could not be streamed to completion; content that could
> not be read is treated as failed, not passed

Both are `F`, and both are the publisher's — so, unlike the truncation case, no grade was wrong.
What was wrong is that the published sentence asserted something the tool had not observed. It
said the array could not be read *from the document*. In the landing-page case there was no
document: the server answered a request for a machine-readable file with a page for a person to
look at, and the sentence attributed that to the file's contents.

`Content-Type` — the one thing the server ever said about *what* it was sending — was read
nowhere and stored nowhere. The tool had no way to say "this URL served a web page", so it said
the only thing it could.

## The fix

**`FetchOutcome` and the cache metadata now record the declared `Content-Type` verbatim**, on
every path that has response headers: the stored body, the 304 revalidation, the oversized
refusal, the unstorable body, and the HTTP error. It is recorded exactly as sent, parameters
included, because it is evidence about a response rather than a value the fetcher interprets.

A 304 carries no body and usually no `Content-Type`. The declaration that describes the bytes
being revalidated is the one made when they were downloaded, so it is carried in the cache
metadata: a 304 that repeats it may refresh it, and a 304 that omits it must not erase it.

**The grade reason then states the evidence instead of only the symptom.** Where the document
did not stream *and* the server declared a media type whose purpose is to be rendered for a
person — `text/html`, `application/xhtml+xml` — the published sentence is:

> the server declared Content-Type 'text/html' — a web page, not the requested file — and the
> standard_charge_information array could not be streamed to completion; content that could not
> be read is treated as failed, not passed

Where a media type was declared and is not one of those, it is named without inference, because
a server may serve HTML under any label and this tool cannot check:

> the standard_charge_information array could not be streamed to completion; the server declared
> Content-Type 'application/json'; content that could not be read is treated as failed, not
> passed

## What deliberately did not change

**`Content-Type` is not a grading input, and this change does not make it one.** A conforming
MRF served as `text/html` scored `A` before this change, correctly, and still does — there is a
test that fails if that stops being true. Refusing a body on a header would fail a publisher for
something this tool has no basis to judge; servers mislabel large static files routinely, and
the file's contents answer the question the header could only have hinted at.

The header is therefore consulted at exactly one point: *after* a document has already failed to
stream. It explains a failure that happened; it never causes one. Concretely:

- The fetcher records it and acts on it nowhere. A `text/html` 200 is `fetched` as before.
- `_retrievability` does not read it, so the retrievability dimension of a persisted record
  still derives from the same evidence it always did, and records written before this change
  still verify.
- The grade rule table is byte-identical and `GRADE_POLICY_FINGERPRINT` is unchanged. No grade
  in any cohort moves, so announcing a regrade would announce something that did not happen
  (the same reasoning ADR 0005 and `COMPARISON_VERSION` 2 already record).

**Where no declaration was recorded, the historical sentence is reproduced exactly.** The six
assessments in the [2026-08-14 cohort](../../data/cohorts/2026-08-14.json) were captured before
`Content-Type` was recorded, so they carry none, and their published sentences are unchanged.
This is not incidental: an unrecorded header and a server that declared nothing are different
facts, and neither of them is evidence that the wrong document arrived. A tool that filled that
absence with a claim would be committing this repository's most familiar defect — an absence
rendered as a value — while fixing another one.

## How often the guard can fire, measured

On 2026-08-18 each of the six URLs in the 2026-08-14 cohort was sent one `HEAD` request with an
identifying User-Agent, `robots.txt` consulted first through this project's own `Politeness`
gate and the default per-host interval held between requests. **No response body was
downloaded.** All six returned HTTP 200 with `Content-Type: application/json`.

So on that date, on that network, none of the six was serving a landing page, and this
distinction would have changed no published sentence. That is the expected result and not an
argument against the change: the case being fixed is the one where a URL that served a file
yesterday serves a page today, which is only ever observed after the fact. What the measurement
establishes is that the cohort's baseline is six declared `application/json` responses, so any
future `text/html` on those URLs is a change from a recorded starting point rather than an
unmeasured assumption.

**Limit of this measurement.** It is HEAD, on one date, from one network. A server may frame a
GET differently from a HEAD, and a media type is a declaration rather than a proof — a server
may serve JSON as `text/html` or an interstitial as `application/json`. That is exactly why the
declaration is only ever read alongside the parse result, and never on its own.

## What did not transfer

The sibling instance in `femtech-privacy-eval` shares the rule and needed a different second
half, which is worth recording because the temptation is to reuse the mechanism rather than the
rule.

There, an HTTP 200 returning a Cloudflare interstitial or a consent wall is stored as a policy
snapshot and re-extracts to zero claims, which then diffs as a promise *removed* by a named
company. Recording `Content-Type` would not help at all: the interstitial and the genuine
privacy policy are both `text/html`, so the declaration carries no signal there. What arrived
had to be characterised structurally instead — by whether the capture retained any of the
extractable structure that watch target had already demonstrated — and the refusal had to run
in both directions, because a false exoneration is as bad as a false accusation.

The general rule held in both places. The specific evidence that made it actionable did not.

## What is still open

- A zero-byte HTTP 200 is still described by the base sentence when the server declared no media
  type. It is a distinct event — the server answered with nothing — and `size_bytes` is already
  recorded, so this is a reason-stage change with no new evidence needed.
- Nothing verifies a declared length or media type against a `HEAD` or `Range` probe before the
  download begins; the check here remains after the fact, on the response that arrived.
- A media type is a declaration, not a proof. A landing page served as `application/json` is
  still described by the base sentence, and this tool cannot currently tell that case from a
  malformed file.
