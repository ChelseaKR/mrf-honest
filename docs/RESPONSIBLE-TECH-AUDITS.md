# Responsible-Tech Audits: mrf-honest

Instantiates the portfolio's RESPONSIBLE-TECH-FRAMEWORK for this repository. Created 2026-08-07
as part of the standards conformance pass. Append-only, like ADRs: deeper artifacts get added to
this file, not written as replacements.

**Status: declarations for the current scope (phases 0-1), not a full pass.** Every line below
is accurate as of 2026-08-07. The project has no deployed surface, no users other than the
maintainer, and no model component, which keeps most sections small today; the same sections
must be re-run before phase 4 (published comparisons) and phase 5 (public site).

## Applicability

- **A Ethics:** applies (declarations below)
- **B Bias:** applies (methodology-level, below)
- **C Privacy:** applies (data inventory below)
- **D Transparency:** applies (below)
- **E Accessibility:** N/A today, no human-facing HTML; in scope at phase 5
- **F Security:** applies (below)
- **AI evaluation:** N/A, no LLM or model component; the grading and comparison path is
  deterministic by design (IMPLEMENTATION-PLAN, "Engineering standards, inherited")
- **I18N:** N/A, see `docs/I18N.md`

## A. Ethics

The project grades *files*, not organizations, and certainly not care. It does not rank
hospitals or payers as good or bad, does not advise anyone what to pay, and does not claim a
rate in an MRF is a quote or medical advice. These are written non-goals
(IMPLEMENTATION-PLAN, "Deliberately not doing") and any feature that crosses them needs an ADR
that supersedes this section.

Fetching is polite by design constraint: identified client, robots and rate limits honored,
stop if asked. Files are fetched only because publishers are legally required to post them.

## B. Bias

The known hazard is statistical, not demographic: mixing incommensurable rate methodologies
(fixed dollar, percentage-of-billed, per diem) produces confidently wrong comparisons, and
phase 0 measured the hazard in live data (5,909 percentage-based rates in the same array as
190,000+ dollar amounts in one file). The committed mitigations are structural: never average
across arrangement types (segment or refuse), small-cell suppression before display, and
uncertainty intervals on every published comparison. A bias review of grading dimensions
against publisher size and resources (a small rural hospital and a national payer do not have
the same publishing budget) is owed before phase 3 grades are published.

## C. Privacy

Data inventory today: public price-transparency files (prices, institutional identifiers such
as EINs, no individual-level data), fetched to a local cache that is gitignored. No PHI, no
user data, no telemetry, no accounts. Standing rule: if a fetched file is ever found to contain
individual-level data, that is a disclosure incident to report to the publisher, never a
dataset to analyze (`docs/CONTEXT.md`; also restated in `SECURITY.md`).

## D. Transparency

The methodology is public by design, uncertainty and suppression are carried structurally, and
the project publishes its own errors: `docs/PHASE-0-FINDINGS.md` documents the buffer-refill
corruption bug found during phase 1, including why every test passed while it was live. The
phase-5 write-up owes a "what this project got wrong" section as a deliverable.

## F. Security

Runtime dependency count is zero (stdlib-only streaming core, ADR 0002), which is most of the
supply-chain surface a phase-1 repo can remove. Dev toolchain is floor-pinned and locked
(`uv.lock`). `make verify` runs ruff's security (`S`) rules on every pass. No secrets exist in
the repo (nothing to deploy, no credentials needed to fetch public files). CI security scanning
(SAST, secret scan, dependency audit) is owed when the repo gains a remote and CI, tracked in
the README conformance table as a phase-5 gap.
