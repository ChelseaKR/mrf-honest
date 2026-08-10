# Data landscape: what MRFs actually are

Written from working knowledge as of 2026-08-05 and source-checked for the implemented hospital
path on 2026-08-09. **Verify every schema detail against CMS's own documentation before extending
code**, because the templates have changed more than once and this document will drift.

Primary sources of truth: CMS publishes payer schemas and validator tooling at
[`CMSgov/price-transparency-guide`](https://github.com/CMSgov/price-transparency-guide) and the
implemented hospital JSON v3 template at
[`CMSgov/hospital-price-transparency`](https://github.com/CMSgov/hospital-price-transparency/tree/master/documentation/JSON).
Start there, not here.

## Two different rules, two different datasets

### 1. Transparency in Coverage (payers)

Non-grandfathered group health plans and health insurance issuers must publish machine-readable
rate files. Structure:

- A **table-of-contents / index file** may list the actual MRF URLs and is required when one index
  groups multiple plans; a single-plan publication need not have an index
- **In-network rates** files: negotiated rates by provider, billing code, and arrangement
- **Allowed amounts** files: out-of-network historical allowed amounts

CMS requires a non-proprietary open format and lists JSON, XML, and YAML; neither JSON nor gzip is
universal. **Scale is the defining problem.** Individual payer files
routinely run to tens of gigabytes and the largest are far bigger. Nothing about this fits in
memory, and naive tooling dies immediately. This is precisely why the project is worth building:
the constraint is real rather than simulated.

### 2. Hospital Price Transparency (hospitals)

Hospitals must publish a machine-readable file of standard charges: gross charges, discounted cash
prices, payer-specific negotiated rates, and de-identified min/max. CMS moved to a **required
template** (CSV or JSON) with a defined schema, which made this dataset substantially more
tractable than it was before. Hospitals must also publish a consumer-facing display of shoppable
services, which is out of scope here.

Hospital files are the better starting point: smaller, standardized, and there are thousands of
publishers, which is a genuine registry rather than a handful.

For discovery, CMS's current `cms-hpt.txt` convention uses five fields per location
(`location-name`, `source-page-url`, `mrf-url`, `contact-name`, and `contact-email`) and permits
multiple repeated location blocks. The document belongs at the root of the public site selected
to host the MRF, which may be a health-system or vendor origin rather than the hospital's corporate
domain. The `mrf-url` identifies the direct download. CMS expressly permits the root TXT URL itself
to redirect; this project's separately validated HTTPS redirect handling for an MRF request is a
transport policy, not a claimed CMS format requirement. See the
[CMS TXT FAQ](https://www.cms.gov/files/document/hospital-price-transparency-txt-file-frequently-asked-questions-faqs.pdf)
and [technical generator](https://cmsgov.github.io/hpt-tool/txt-generator/).

## Known pitfalls, all of which are the actual product

These are the reasons the data is hard, and grading them is the point:

- **Technically valid, practically useless.** A file can satisfy the schema and still be
  unusable: every rate reported as a percentage of an undisclosed fee schedule, or codes without
  the context to interpret them.
- **URL rot.** Published locations move, and a discovery link or index goes stale. Any registry must record
  when a URL was last confirmed and distinguish "gone" from "we could not reach it," which is the
  exact lesson `fhir-scorecard` learned when TLS interception made a live endpoint look dead.
- **Fetch hostility.** For hospital MRFs, account, password, PII, and automation barriers are
  expressly barred. CDN or bot controls matter when they actually prevent the required access;
  record the observed result as a finding rather than inferring from the technology alone.
- **Enormous files with thin content.** Size does not equal completeness. A 40GB file can carry
  less usable rate information than a 200MB one.
- **Identifier inconsistency.** NPI and TIN reporting varies in quality; provider groups are
  represented inconsistently; the same entity appears many ways.
- **Code-set drift.** CPT, HCPCS, DRG, NDC, and revenue codes all update on their own calendars.
  Any modeling layer needs code-set versioning or comparisons silently break year over year.
- **Rates that are not prices.** Percentage-of-billed and per-diem arrangements are not
  comparable to fixed dollar amounts, and averaging across them produces confident nonsense.
  **This is the single easiest way to publish a wrong number, and avoiding it is the thesis.**

## Who is already here

Turquoise Health, Serif Health, Payless Health, and others parse this data commercially, some of
them very well funded. **This project is not first and must never claim to be.**

What is genuinely underserved: an open, reproducible methodology; per-publisher quality grading
that says plainly when a file is useless; and comparisons that carry their own uncertainty. A
commercial product has a structural incentive to present its coverage as more complete and more
comparable than it is. An open one does not, and that asymmetry is the entire reason to build.

## Legal and ethical posture

- These files are **required to be publicly posted**. Retrieving them is the intended use.
- Fetch politely: identify the client with a contact address, honor robots and rate limits, do not
  parallelize aggressively against one publisher, and cache so a file is fetched once.
- **No intended PHI.** The required MRF schemas do not call for patient data, so MRFs should not
  contain PHI. If individual-level data ever appears in one,
  that is a disclosure incident to report to the publisher, not a dataset to analyze. Say so in
  the repository before it happens.
- Grades describe observable properties of published files. **Not compliance determinations**, and
  never a claim about what care is worth or what an organization should charge.
