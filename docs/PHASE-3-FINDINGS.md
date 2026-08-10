# Phase 3 findings: failures are rows, not missing publishers

Measured 2026-08-09 on the phase-3 tree. The full gate passed with 226 tests at 89.78% branch
coverage, zero ruff findings, and zero strict-mypy errors. The scorecard acceptance is deterministic
and fixture-based; no live multi-publisher grade distribution is claimed.

## Result

Phase 3 is implemented as a separate remote assessment artifact rather than a mutation of the
content-derived lakehouse run. `mrf-honest scorecard` (alias `grade`) performs one identified,
bounded retrieval and writes exactly one `FileAssessment` for each structured terminal fetch
outcome. A failed HTTP or network request is therefore a dated row with a reason, not an absent
publisher.

The assessment composes five independent dimensions and no overall rank:

- retrievability comes from the current fetch attempt;
- conformance, completeness, interpretability, and freshness come from the local inspector only
  when the exact admitted body is verified by digest and size;
- a post-inspection rehash detects ordinary cache mutation during the scan; and
- without a verified body, all four local dimensions are explicitly `NOT_ASSESSED`.

Every record retains `targeted`, `network_attempted`, `verified_body_available`,
`inspection_performed`, and `inspection_scan_completed`. These fields keep an unreachable or
invalid target visible without pretending that every target reached the network or completed a
scan.

## Attribution boundary

The deterministic retrieval mapping is deliberately asymmetric:

| Outcome | Result | Why |
|---|---|---|
| Verified `fetched` or `not_modified` body plus inspection | `OBSERVED` | The remote observation and exact local body agree. |
| HTTP, network, or decoded-content failure | `FINDINGS` | A request began and produced dated technical evidence. |
| Unsafe redirect observed after a request began | `FINDINGS` | The remote path produced the unsafe target. |
| URL invalid before an attempt | `NOT_ASSESSED` | Caller provenance is not independently linked to discovery evidence in v1. |
| Configured decoded-size ceiling | `NOT_ASSESSED` | A project limit is not publisher unavailability. |
| Cache miss/error or local body-integrity failure | `NOT_ASSESSED` | Local infrastructure is not attributed to the publisher. |

HTTP 401 and 403 use `MRF_AUTOMATION_BARRIER_OBSERVED`; other remote direct-download failures use
`MRF_DIRECT_DOWNLOAD_FAILED`. Both preserve the exact fetch status, attempt count, observation time,
HTTP status when available, final public URL, and bounded reason. Neither is labeled a legal
compliance determination.

## Identity, integrity, privacy, and comparison

The scorecard has two hashes with different jobs:

1. A portable semantic assessment ID covers stable publisher/type/location/URL identity, sanitized
   retrieval evidence, policy, observation, and inspected content. Correcting a display name or
   moving the cache does not change it.
2. `assessment_body_sha256` covers the complete public record. Changing a note, finding, display
   value, coverage flag, or nested evidence fails registry verification.

The public artifact omits cache paths and operator contact values. URL queries and fragments are
removed while SHA-256 of each exact URL preserves equality; server/OS error text is line-normalized,
path-redacted, and bounded. Credential-bearing subject URLs are rejected before persistence.

Direct comparison requires identical publisher type, profile, URL provenance, assessment policy,
retrieval policy, execution-strategy marker, and UTC `as_of` date. The implemented profile accepts
`hospital` only; `payer` is reserved and rejected until an adapter exists. Historical policy
fingerprints remain readable but naturally fall into a different comparison scope.

Injected execution hooks are persisted as `custom` evidence but are categorically non-comparable;
an undifferentiated marker cannot identify arbitrary adapter code. Version 1 also lacks a persisted
collector-run/network-vantage identifier, so matching default-policy rows still require a
caller-known controlled run. Phase 4 must encode that context before publishing remote comparisons.

## Discovery correction made during phase 3

Source review found that CMS currently specifies five attributes per `cms-hpt.txt` location, not
three, and permits multiple repeated location entries in one file. The discovery model now retains
all five (`location-name`, `source-page-url`, `mrf-url`, `contact-name`, `contact-email`), ordered
multi-location entries, per-entry extras/problems, and document problems. Registry schema v2 writes
the full entry list and reads v1 single-entry history without inventing missing contact evidence.
Those CMS-required POC fields are public professional contact data. They remain local discovery
evidence, are not copied into scorecard artifacts, and are not used for analysis or outreach.
`data/registry*.jsonl` is ignored; operators should retain raw discovery/contact evidence only as
long as needed to reproduce and audit discovery. Phase 4 must set a concrete cohort retention rule.

The conventional TXT belongs at the confirmed MRF-hosting origin, which may be a health-system or
vendor site. An absent TXT on a guessed hospital corporate domain is not publisher evidence.

## Limits retained on purpose

- No real cohort has been collected or published. The phase-3 claim is about the executable method,
  persistence, and tested failure semantics.
- Caller-selected `cms_hpt` provenance is not yet linked by discovery-record digest. It participates
  in identity/scope but cannot by itself attribute an attempts-zero invalid URL.
- Version 1 never joins a current failed request to an older cached inspection. Such a join needs an
  explicit prior-assessment/body-evidence digest and both observation dates.
- The assessment registry is an atomic single-writer local artifact. Concurrent writers and a
  scheduled service are unsupported.
- Broad collection remains blocked on `robots.txt` policy, per-host pacing, and `Retry-After`
  handling.
- HTTPS-only retrieval is this project's security policy, not a CMS requirement. MIME type, HEAD,
  range support, ETag, Last-Modified, and Content-Length are not graded as CMS requirements.

## Primary sources

- [45 CFR § 180.50](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50)
- [CMS Hospital Price Transparency policy FAQs, June 2026](https://www.cms.gov/files/document/hpt-policy-faqs-june-2026.pdf)
- [CMS hospital price-transparency TXT FAQ](https://www.cms.gov/files/document/hospital-price-transparency-txt-file-frequently-asked-questions-faqs.pdf)
- [CMS TXT technical generator](https://cmsgov.github.io/hpt-tool/txt-generator/)
- [CMS hospital JSON v3 data dictionary](https://github.com/CMSgov/hospital-price-transparency/blob/master/documentation/JSON/README.md)
