# Responsible-Tech Audits: mrf-honest

Instantiates the portfolio's RESPONSIBLE-TECH-FRAMEWORK for this repository. Created 2026-08-07
as part of the standards conformance pass. Append-only, like ADRs: deeper artifacts get added to
this file, not written as replacements.

**Status: declarations for the current local scope (phases 0-2 and partial phase 3), not a full
publication pass.** Every line below is accurate as of 2026-08-09. The project has no deployed
surface, no users other than the maintainer, and no model component; the same sections must be
re-run before phase 4 (published comparisons) and phase 5 (public site).

## Applicability

- **A Ethics:** applies (declarations below)
- **B Bias:** applies (methodology-level, below)
- **C Privacy:** applies (data inventory below)
- **D Transparency:** applies (below)
- **E Accessibility:** N/A today, no human-facing HTML; in scope at phase 5
- **F Security:** applies (below)
- **AI evaluation:** N/A, no LLM or model component; the grading and comparison path is
  deterministic by design (IMPLEMENTATION-PLAN, "Engineering standards, inherited")
- **I18N:** applies narrowly to the English-only operator CLI, see `docs/I18N.md`

## A. Ethics

The project grades *files*, not organizations, and certainly not care. It does not rank
hospitals or payers as good or bad, does not advise anyone what to pay, and does not claim a
rate in an MRF is a quote or medical advice. These are written non-goals
(IMPLEMENTATION-PLAN, "Deliberately not doing") and any feature that crosses them needs an ADR
that supersedes this section.

Fetching is bounded and identified: the client requires a contact, uses conditional requests and
retry backoff, validates HTTPS redirects, limits decoded bytes, and caches content by digest. The
current fetcher does **not** autonomously retrieve or enforce `robots.txt`; broad scheduled
collection must not launch until that gap is resolved or documented by a superseding decision.
Files are fetched only from publisher-provided or CMS-conventional locations, never guessed MRF
paths.

## B. Bias

The known hazard is statistical, not demographic: mixing incommensurable rate methodologies
(fixed dollar, percentage-of-billed, per diem) produces confidently wrong comparisons. The
2026-08-09 acceptance observed 192,778 dollar, 5,909 percentage, and 48,736 algorithm
representations in one file (`docs/PHASE-2-FINDINGS.md`). The committed mitigations are structural: never average
across arrangement types (segment or refuse), small-cell suppression before display, and
uncertainty intervals on every published comparison. A bias review of grading dimensions
against publisher size and resources (a small rural hospital and a national payer do not have
the same publishing budget) is owed before phase 3 grades are published.

## C. Privacy

Data inventory today: public price-transparency files (prices and institutional identifiers, no
intended individual-level data), fetch metadata and failure reasons, content-addressed local cache,
DuckDB catalog, Parquet snapshots, and immutable run manifests. Generated data directories are
gitignored. No user data, telemetry, or accounts. Standing rule: if a fetched file is ever found to contain
individual-level data, that is a disclosure incident to report to the publisher, never a
dataset to analyze (`docs/CONTEXT.md`; also restated in `SECURITY.md`).

## D. Transparency

The methodology and complete finding catalog are public. Inspections preserve independent
dimensions with citations and deliberately produce no composite grade or compliance label.
Percentage and algorithm rates are structurally excluded from the dollar-comparison mart.
`docs/PHASE-0-FINDINGS.md` documents the buffer-refill corruption bug found during phase 1,
including why every test passed while it was live. The phase-5 write-up still owes a broader
"what this project got wrong" section.

## F. Security

The base streaming, discovery, retrieval, registry, and inspection path has no third-party runtime
dependency (ADR 0002). DuckDB is an explicit optional lakehouse dependency (ADR 0003). The dev
toolchain and optional dependency are locked in `uv.lock`; `make verify` runs ruff's security (`S`)
rules. Retrieval accepts public HTTPS URLs only, rejects credentials and unsafe redirects, limits
decoded size, verifies cached blob digests, and writes metadata atomically. The SHA-pinned CI
workflow is committed but has no observed hosted run; SAST, secret scanning, and dependency-audit
jobs remain open before publication.
