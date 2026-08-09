# Implementation plan

Phased so each phase produces something defensible on its own. **If the project stalls after any
phase, what exists should still be honest and useful.** That is the same property that made
`fhir-scorecard` worth publishing at 19 endpoints.

Target for a defensible v0.1: **phases 0 through 3**. Phases 4 and 5 are what make it citable.

---

## Phase 0: prove the constraint is real

*Goal: confirm the hard part is hard before designing around it. Half a day.*

- [ ] Pull CMS's current payer schema and validator from `CMSgov/price-transparency-guide`
- [ ] Fetch **one** hospital file and **one** payer index file by hand. Record actual byte sizes.
- [ ] Attempt a naive `json.load()` on the payer file and watch it fail. Write down the number.
- [x] Confirm the current hospital template schema from CMS documentation, not from memory

**Stop condition:** if hospital files turn out to be uniformly small and clean, the data-platform
premise weakens and the project should be rescoped or dropped. Better to learn that in half a day.

## Phase 1: streaming ingestion

*Goal: read files that do not fit in memory, without cleverness that cannot be explained.*

- [x] Incremental JSON reader plus a bounded fetch layer that decodes gzip to the verified cache
- [x] A registry of publishers with the same verification discipline as `fhir-scorecard`: no
      guessed URLs, every entry records how and when it was confirmed
- [x] Polite fetcher: identifying User-Agent with contact, conditional requests, on-disk cache so
      a file is retrieved once, backoff, and **error messages that name the cause**. The
      `fhir-scorecard` lesson applies directly: a bare `URLError` once caused a live endpoint to
      be recorded as dead.
- [x] Record fetch outcomes per publisher as data, including failures, with dates

The implemented fetch path is operator-invoked and serial. A broad scheduled collector is not yet
authorized by this checkbox: `robots.txt` policy, per-host pacing, and `Retry-After` handling remain
explicit prerequisites in the responsible-tech audit.

**Deliverable:** can ingest the largest file found in phase 0 on a laptop, with bounded memory.
Measure and publish the peak memory and wall time; those numbers are the credibility.

## Phase 2: the lakehouse

*Goal: the piece that closes the actual gap. Do not skip the modeling rigor to get to results.*

- [x] Archive the exact admitted source by content SHA-256 and export all 13 declared models as
      **partitioned Parquet** (publisher, period, file version, run identity)
- [x] **DuckDB** as the local query engine and catalog alongside portable Parquet exports
- [x] A staging → intermediate → mart model layering, dbt-shaped, with each model documented
- [x] **Data contracts enforced at layer boundaries**: expected columns, types, nullability,
      accepted code sets, referential expectations. A contract violation fails the build; it does
      not warn.
- [x] Idempotent loads keyed by pipeline version + publisher + content + inspection `as_of` +
      transformation fingerprint, including inspection policy/catalog identity, with stable
      source-scoped keys per rate representation
- [x] Retain exact raw item/modifier text and hashes; project typed numerics as `DECIMAL(38,10)`;
      retain every item code in ordered `codes_json`; and expose exact/canonical/unresolved
      modifier context rather than inventing a primary code or silently dropping failed joins
- [x] Resolve modifiers by charge setting, report selected/candidate settings and explicit setting
      mismatches, allow disjoint inpatient/outpatient definitions, and reject applicable-setting
      overlap
- [x] Schema-v4 run manifests with a digest over every immutable body field, content-addressed
      artifact integrity, and recoverable `prepared` → catalog commit → `success` finalization
- [x] Record cost signals per model: bytes scanned, rows produced, wall time. Query cost thinking
      is half of what data-platform interviews are actually probing.

**Deliverable status:** the model DAG and methodology-safe multi-publisher query are documented.
The post-audit `hospital-json-v2` path passed the 153-test gate and a clean real-file acceptance:
30,114 items, 13 Parquets, zero parser problems or raw hash mismatches, 46.66 seconds wall time,
and 534,790,144 bytes maximum process RSS. The real file's 11 modifier definitions omitted their
optional setting, so synthetic regressions—not the real run—establish setting mismatch, disjoint
definition, and overlap-rejection behavior. An empirically executed multi-publisher result remains
open. Concurrent writers, historical warehouse migrations, and a full SIGKILL/fsync crash matrix
are not represented as complete.

## Phase 3: per-publisher quality grades

*Goal: the scorecard pattern, which is the proven part of this portfolio.*

Candidate dimensions, each deterministic and each citing the rule or schema clause it rests on:

| Dimension | Asks |
|---|---|
| Retrievability | Is the file where it is supposed to be, fetchable, and not behind a wall? |
| Conformance | Does it validate against the current CMS schema? |
| Completeness | Are the fields that make a rate interpretable actually populated? |
| Interpretability | Are rates expressed as usable amounts, or as percentages of undisclosed schedules? |
| Freshness | Is it updated on the required cadence, and does the file say when? |

- [ ] Fail closed: unretrievable is a stated grade with a reason, never a gap in the dataset
- [ ] Grades comparable **within publisher type only** (hospital versus payer), the same rule
      `fhir-scorecard` enforces across kinds
- [x] A `docs/how-we-grade` page where every finding code links to its citation

## Phase 4: honest comparison

*Goal: the thesis. If this phase is done badly the project should not ship.*

- [x] **Never average across arrangement types.** Fixed dollar, percentage-of-billed, and per-diem
      rates are not commensurable. Segment or refuse.
- [ ] **Small-cell suppression** before anything is displayed, with the threshold stated
- [ ] **Uncertainty intervals** on every published comparison, using the same discipline as
      `nearmiss` (Wilson and Poisson intervals, exposure normalization where relevant)
- [ ] Explicit denominators everywhere: "of the publishers we could retrieve and parse" is a
      different population from "of all hospitals," and conflating them is exactly the error
      `fhir-scorecard` had to publicly correct when it merged guessed URLs with documented ones
- [ ] Refuse to publish a comparison that cannot carry its own uncertainty

## Phase 5: publish

*Goal: match the established pattern; most of this is mechanical by now.*

- [ ] Static site: per-publisher pages, category indexes, methodology page, sitemap, JSON-LD
- [ ] `dataset.csv` plus a Table Schema description, and a static JSON API
- [ ] `CITATION.cff` and dated releases
- [ ] MCP server over the published dataset, read-only, with a `grading_method` tool returning the
      documented limits
- [ ] CI: quality/build gates are committed with SHA-pinned actions; scheduled refresh and a
      first observed hosted run remain open
- [ ] A claim and correction flow, non-adversarial, honoring removal requests without demanding
      proof
- [ ] A write-up in the pattern of `docs/payer-verifiability.md`, **including a section on what
      this project got wrong**

---

## Engineering standards, inherited

Same house rules as the rest of the portfolio, non-negotiable:

- `make verify`: ruff with security rules, mypy strict, pytest with a branch-coverage floor
- Deterministic core; no model anywhere in the grading or comparison path
- Property-based tests on the statistical layer specifically
- Every published number traceable to a query and a source file
- Zero fabricated figures, including in the README

## Deliberately not doing

- **Ranking hospitals or payers as good or bad.** This grades *files*, not organizations, and
  certainly not care.
- **Advising anyone what to pay.** A rate in an MRF is not a quote and not medical advice.
- **Claiming novelty.** Commercial products exist. The differentiator is the open methodology and
  the honest statistics, not being first.
- **Scaling to every publisher before the method is right.** Fifty publishers with a defensible
  method beats five thousand with a broken one, which is the same call already made once when
  three verified servers were held out of `fhir-scorecard` until grading was version-aware.
