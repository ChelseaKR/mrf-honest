# 0004. Separate, integrity-hashed remote scorecard artifacts

## Status

Accepted - 2026-08-09

## Context

The local CMS hospital JSON v3 inspector can repeatably assess conformance, completeness,
interpretability, and freshness from exact bytes and an explicit date. It intentionally cannot
infer whether a URL is currently retrievable. Conversely, a remote result changes without any
change to the file body: a URL can return 200, 304, 403, 404, timeout, or a transient cache error on
different observations. Adding that mutable evidence to the phase-2 lakehouse run identity would
either reuse stale remote facts or rebuild content-derived models for an unrelated network change.

An omitted publisher after a failed fetch would also bias every future denominator. At the same
time, operator mistakes, configured byte limits, and local cache failures must not be labeled as
publisher failures. CMS requires direct automated access, but this project reports dated technical
observations rather than legal compliance conclusions.

## Decision

1. Keep `inspect_hospital_file` local-only. Build a separate `FileAssessment` that composes one
   dated `FetchOutcome` with an inspection only when the admitted body's SHA-256 and size match and
   a post-inspection rehash confirms the cache did not change during the scan.
2. Persist exactly one assessment row for every structured terminal fetch outcome. HTTP, network,
   content, and unsafe-redirect observations become source-cited retrievability findings. Invalid
   pre-network input, a configured size ceiling, cache miss, and cache error remain explicitly
   `NOT_ASSESSED`; they are not attributed to a publisher.
3. Require explicit subject identity: publisher identifier, publisher type, location identifier,
   requested URL, and URL provenance. The current adapter accepts `hospital` only. `payer` is a
   reserved explicit type and fails closed until a payer assessment profile exists.
4. Do not trust caller-asserted `cms_hpt` provenance enough to attribute an attempts-zero invalid
   URL. Version 1 records the provenance in identity and comparison scope but does not yet embed a
   discovery-record digest.
5. Derive assessment `as_of` from the retrieval attempt's UTC date. File freshness continues to use
   the MRF's `last_updated_on`; HTTP metadata and TXT age are not substitutes.
6. Publish five independent dimension statuses and no composite. When no verified body exists,
   the four local dimensions are `NOT_ASSESSED`. Retain explicit coverage fields for target,
   network attempt, verified body, inspection, and completed scan.
7. Make comparison scope include publisher type, profile, URL provenance, assessment-policy
   fingerprint, retrieval-policy fingerprint, and `as_of`. `require_comparable` rejects any mixed
   scope. The retrieval fingerprint includes execution limits and whether default or injected
   execution was used, but not the operator's contact. Any injected opener, sleeper, backoff, or
   clock is retained as `custom` evidence and categorically refused for direct comparison because
   the marker cannot fully identify arbitrary code.
8. Sanitize the durable artifact: omit cache paths and the operator contact; remove URL queries and
   fragments while retaining SHA-256 of the exact URLs; bound and redact error strings. Reject
   credential-bearing subject URLs before persistence.
9. Give each assessment a portable semantic ID over stable subject identity, remote evidence,
   policy, observation, and inspected content. Keep publisher display name out of that ID. Separately
   digest the complete record body so any persisted field change is detected.
10. Use a single-writer `AssessmentRegistry`. Before each logical append, verify all existing and
    new records, write the complete JSONL to a same-directory temporary file, fsync it, atomically
    replace the registry, and fsync the directory. Concurrent writers are unsupported.
11. Keep historical assessment and inspection fingerprints readable. Fingerprint changes make
    comparison scopes disjoint; they do not retroactively corrupt otherwise intact evidence.
    The known current assessment fingerprint is bound to its policy-version and inspection-
    fingerprint components; older fingerprints remain opaque. Status semantics are versioned with
    the assessment record schema.
12. Version 1 never joins a current failed retrieval to an older cached inspection. A future join
    must carry an explicit prior-assessment/body-evidence digest and preserve both observation dates.

## Consequences

- An HTTP or network failure is a durable assessment row, so unreachable targets remain in honest
  denominators instead of vanishing.
- Local infrastructure ambiguity stays visible without becoming an accusation against a hospital.
- Remote changes do not invalidate or duplicate content-derived lakehouse runs.
- The semantic assessment ID is portable across cache roots, while the body digest detects changes
  to display metadata, notes, findings, or coverage fields.
- Atomic whole-file replacement makes this suitable for a local, serial operator workflow, not a
  scheduled or concurrent collector. Broad collection still requires `robots.txt` policy,
  per-host pacing, and `Retry-After` handling.
- Query-token removal means the public artifact cannot reproduce a signed URL from cleartext. The
  exact URL digest proves identity equality without disclosing the token; operators must retain any
  private raw discovery evidence separately.
- A malformed non-credential URL can become an attempts-zero row. A credential-bearing URL is a
  deliberate privacy/security usage rejection and produces no scorecard artifact.
- Version 1 has no persisted collection-run or network-vantage identifier. Matching default-policy
  rows therefore require a caller-known controlled collection context; phase 4 must encode that
  context before remote comparisons are published.

## Sources

- [45 CFR § 180.50](https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50)
- [CMS Hospital Price Transparency policy FAQs, June 2026](https://www.cms.gov/files/document/hpt-policy-faqs-june-2026.pdf)
- [CMS hospital price-transparency TXT FAQ](https://www.cms.gov/files/document/hospital-price-transparency-txt-file-frequently-asked-questions-faqs.pdf)
