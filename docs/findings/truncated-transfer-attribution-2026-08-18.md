# A download that stopped early was published as a hospital's unreadable file

*An engineering finding about this project, observed 2026-08-18, with a dated measurement of how
six real published MRF endpoints frame their responses. It describes this tool's behaviour and
those servers' HTTP framing on one date. It is not a ranking of any hospital, not a statement
about care, and not a legal compliance determination.*

## The defect

CPython's `http.client` does not raise `IncompleteRead` when a length-delimited response body
ends early. `HTTPResponse.read(amt)` returns `b""` and closes the connection, with a source
comment saying that raising there *"might break compatibility"*. The fetcher read that as
end-of-body.

A partial download therefore looked exactly like a complete, smaller file. It was hashed,
installed in the content-addressed cache alongside the server's validators, recorded as
`fetched`, and handed to the inspector — where the truncated JSON produced a
`JSON_STREAM_INCOMPLETE` **conformance** error and, through the grade policy, an `F` reading:

> the standard_charge_information array could not be streamed to completion; content that could
> not be read is treated as failed, not passed

Measured on the composition path with a body cut to 60% of its declared length, that `F` and its
sentence were byte-identical to the `F` earned by an HTTP 200 that returns an HTML landing page
and by an HTTP 200 with a zero-byte body. Three different events, one published sentence, and in
one of the three the sentence was false: nothing was wrong with the document, the download did
not finish.

That is the exact conflation [`docs/how-we-compare.md`](../how-we-compare.md) forbids and that
`cohort.py` states as a rule in its own module docstring — a local limit attributed to a
publisher — reached here through a path no status mapping covered, because the fetch had
reported success. It also persisted: the truncated blob carried the server's `ETag` and
`Last-Modified`, so the next conditional request would have returned 304 and revalidated the
truncation instead of re-fetching the file.

## The fix

The `Content-Length` the server already sent is the only surviving evidence of a truncation, and
it was being read for one purpose (refusing an oversized file before downloading it) and then
discarded. It is now also compared against the bytes that actually arrived. A disagreement in
either direction is a `network_error` — retried, like the connection reset it is, and never
installed in the cache — whose stated reason names both counts:

> the response body ended after 41 of the 883973507 bytes the server declared in Content-Length

`Content-Length` counts wire bytes, so gzip is compared before decoding. Per RFC 9112 § 6.1 a
declared length beside a `Transfer-Encoding` header means nothing, so it is ignored on both sides
of the read.

## How often the guard can fire, measured

The check only works where a server declares a length. On 2026-08-18 each of the six URLs in the
[2026-08-14 cohort](../../data/cohorts/2026-08-14.json) was sent one `HEAD` request with an
identifying User-Agent, `robots.txt` consulted first through this project's own
`Politeness` gate and the default per-host interval held between requests. **No response body was
downloaded.** All six returned HTTP 200 with `Content-Type: application/json` and
`Accept-Ranges: bytes`.

| Response framing on 2026-08-18 (HEAD) | Files | Declared length |
|---|---|---|
| Identity encoding with `Content-Length` | 3 | 154,579,203 / 112,814,066 / 43,065,577 bytes |
| `Content-Encoding: gzip`, no `Content-Length` | 3 | — |

None sent `Transfer-Encoding`. For the three identity-encoded files — the 43 MB to 155 MB range
where a dropped connection is most likely — the declared length is the only signal that a
transfer ended early, and before this change there was none. For the three compressed ones the
gzip trailer check already caught a short stream, but reported it as `content_error`: a
permanent, never-retried claim that the publisher's file was not valid gzip. That case is now
re-examined against the declared length when one is present and reported as the short transfer it
is.

**Limit of this measurement.** It is HEAD, on one date, from one network. A server may frame a
GET differently from a HEAD — in particular, a server compressing on the fly commonly omits
`Content-Length` on HEAD and may or may not send one on the GET — so the three-of-six split is
evidence about how much of the cohort the guard covers, not a fixed property of those hosts.

## What is still open

- An HTTP 200 that returns an HTML landing page instead of the document is still described by the
  same "could not be streamed to completion" sentence as a genuinely malformed JSON file. Both
  are `F` and both are the publisher's, so no grade is wrong, but the reason is less specific
  than it could be. `Content-Type` is not recorded on the assessment at all today.
- Nothing verifies a declared length against a `HEAD` or `Range` probe before the download
  begins; the check here is after the fact, on the response that arrived.
