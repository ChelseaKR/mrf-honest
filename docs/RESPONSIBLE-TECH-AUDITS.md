# Responsible-Tech Audits: mrf-honest

Instantiates the portfolio's RESPONSIBLE-TECH-FRAMEWORK for this repository. Created 2026-08-07
as part of the standards conformance pass. Append-only, like ADRs: deeper artifacts get added to
this file, not written as replacements.

**Status: declarations for the current local scope (phases 0-3), not a full
publication pass.** Every line below is accurate as of 2026-08-09. The project has no deployed
surface, no users other than the maintainer, and no model component; the same sections must be
re-run before phase 4 (published comparisons) and phase 5 (public site).

> **Superseded in part, 2026-08-16.** Three statements dated 2026-08-09 stopped being true and
> were still being published. The sentence above about no deployed surface is one of them: the
> site has been public since 2026-08-09, as the 2026-08-15 appendix records. See
> [the 2026-08-16 appendix](#appendix-2026-08-16-three-declarations-that-outlived-what-they-described)
> for the other two and for what replaced them. This file is append-only, so the original
> wording stays; the appendix is the current statement.

## Applicability

- **A Ethics:** applies (declarations below)
- **B Bias:** applies (methodology-level, below)
- **C Privacy:** applies (data inventory below)
- **D Transparency:** applies (below)
- **E Accessibility:** in scope as of the published site; WCAG 2.2 AA, gated in CI, with the
  manual screen-reader pass still open (appendices 2026-08-14 and 2026-08-15)
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
[Resolved 2026-08-15; see the 2026-08-16 appendix.]
Files are fetched only from publisher-provided or CMS-conventional locations, never guessed MRF
paths.

The phase-3 scorecard separates publisher observations from operator/infrastructure ambiguity.
HTTP, network, and content failures after an attempt are dated findings; invalid pre-network
input, configured size limits, cache misses, and cache failures are `NOT_ASSESSED`. URL provenance
is required but caller-asserted, so version 1 does not use it alone to attribute invalid input.
Every target remains a row with explicit network/body/inspection/scan coverage flags. No composite
score or legal compliance label is produced.

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
intended patient-level data); public professional contact names and emails CMS requires in TXT location entries;
fetch metadata and failure reasons; content-addressed local cache; DuckDB catalog; Parquet
snapshots; and immutable run manifests. The POC fields remain source evidence in the local
discovery registry, are not copied into scorecard artifacts, and are not used for analysis or
outreach. `data/registry*.jsonl` is gitignored. Operators should keep this contact evidence only as
long as needed to reproduce and audit discovery; phase 4 must set a concrete retention period
before cohort collection. There are no accounts or telemetry. Standing rule: if
a fetched file is ever found to contain individual-level data, that is a disclosure incident to
report to the publisher, never a dataset to analyze (`docs/CONTEXT.md`; also restated in
`SECURITY.md`).

## D. Transparency

The methodology and complete local and remote finding catalogs are public. Inspections preserve independent
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
decoded size, verifies cached blob digests, and writes metadata atomically. Scorecard artifacts
omit local paths and contact values, remove URL query/fragment values while retaining exact-URL
digests, and atomically replace a validated single-writer JSONL registry. The SHA-pinned CI
workflow has observed green public branch and pull-request runs; SAST, secret scanning, and
dependency-audit jobs remain open before publication. [All three shipped; see the 2026-08-16
appendix.]

## Appendix, 2026-08-14: bias review before publishing file grades

Section B recorded a debt: a review of the grading dimensions against publisher size and
resources before any phase-3 grades are published. The comparison layer (ADR 0005) now publishes
per-file letter grades, so this is that review, run against what the policy actually does rather
than what it intends.

**What the policy does to protect small publishers.** The grade counts *dimensions with
structural errors*, not raw finding occurrences, so a small hospital with one systematic export
defect is a `C`, not an `F`, regardless of how many rows the defect touches. `INFO` observations
never lower a grade; warnings alone cap at `B` rather than failing anyone. Nothing in the policy
rewards file size, publisher brand, or engineering budget directly.

**Where the observed hazard actually fell.** In the first collected cohort the resource-shaped
hazard hit a *large* publisher: the project's own decoded-size ceiling nearly excluded the
cohort's biggest file (884 MB) before the ceiling was raised to the documented default. The
policy's answer is structural: a project limit is `NOT_GRADED` with the reason stated, never an
`F`, so a publisher is not penalized for the operator's budget. That rule protects small
publishers on slow hosting for the same reason.

**What this review cannot yet establish.** The first cohort is six files from four large,
well-resourced health systems, selected because their `cms-hpt.txt` documents were discoverable
from origins already recorded in phase 0. It contains no small or rural hospital, so no claim is
made that the observed grade distribution generalizes, and the published copy must state the
cohort's size and composition wherever grades appear. Re-run this review when the cohort grows
past its current composition, and before any aggregate statistic about hospitals as a class is
published.

## Appendix, 2026-08-14: the static site brings accessibility into scope

Section E declared accessibility N/A while no human-facing HTML existed. The comparison site ends
that: every page now ships with `lang="en"`, a skip link that moves focus to `main`, semantic
headings and lists, grade badges with text labels and `aria-label`s (never color alone), status
chips whose information also appears as words, reduced-motion and print media rules, and no
JavaScript at all. What is *not* yet true is also stated: there is no automated accessibility
gate (no axe/pa11y/Lighthouse job) and no manual screen-reader pass has been performed. Those
are open obligations under the portfolio accessibility standard, recorded here rather than
implied as done, and they belong in CI before the site grows past its current handful of pages.

## Appendix, 2026-08-15: the gate ran, and the site was not as accessible as the entry above says

The 2026-08-14 appendix is a fair description of what was built and an unreliable description of
what was true, and the difference is the point of this entry.

The site had been public since 2026-08-09. The portfolio registry, `applicability.yml`, still
carried this repository as `publication: restricted   # local-only: no GitHub remote exists`
with `html: false` and `A11Y: { na: "no HTML surface; early-stage ingestion library only" }`.
`ACCESSIBILITY-STANDARD` section 0 names that registry as its scoping authority, so the section 1
AUTO-GATEs were switched off for a page anyone could load. The claim in the appendix above was
made in good faith by reading the markup; nothing measured it.

When Lighthouse 12.8.2 was finally pointed at the rendered pages on 2026-08-15, two WCAG 2.2 AA
defects were on the live site:

- **SC 1.3.1, `heading-order`.** The index went from `<h1>` straight to the `<h3>` of the first
  file card, with no `<h2>` between. Someone navigating by heading level hears a section that is
  not there. Index accessibility score: 0.98.
- **SC 1.4.3, `color-contrast`.** The `FINDINGS` status chip rendered `#a35d00` on `#f6ead8` at
  11.2px bold: 4.28:1 against a 4.5:1 requirement. It appeared on every file page that recorded
  a warning finding, and the same pair was used by the `WARNING` severity chip. Affected pages
  scored 0.95.

Both are fixed. The contrast fix is a new `--c-ink` token at 5.53:1, because the amber that works
as a badge background does not work as small text on the amber wash.

What changed structurally is more important than either defect. The palette now lives in one
`PALETTE` mapping with a declared table of every text-on-background pair, asserted at 4.5:1 by a
test in `make verify`; a colour added without a declared pair fails the suite. Heading order is
asserted on every generated page by the same suite. In CI, `accessibility.yml` renders the site
from the committed comparison, enumerates every HTML file the render produced, audits each one,
and fails when the page list is short, when a report is missing, or when a category score is
absent — because the usual way an accessibility job stops being a gate is not a wrong threshold,
it is a run that measured nothing and exited 0.

Still open, and not implied as done: **the manual screen-reader pass**. Automated tooling catches
roughly 30-57% of WCAG violations; the rest needs a person. That obligation is unchanged by
anything above.

One thing found while wiring this up, recorded because it affects any repository copying the
pattern: **Lighthouse's `--budget-path` flag does not exist in Lighthouse 12.** On 12.8.2 the
CLI's `--help` lists no budget option, `configSettings.budgets` comes back `null`, and no
`performance-budget` audit is emitted — and the CLI accepts the unknown flag in silence and exits
0, exactly as it does for `--this-flag-does-not-exist=42`. A workflow passing `--budget-path`
looks like it has a resource budget and has none. The budget here is asserted in
`perf/score_lighthouse.py` against the `resource-summary` audit instead.

## Appendix, 2026-08-16: three declarations that outlived what they described

The 2026-08-15 appendix above is about a gate that was scoped out of a live site. This one is
about the same failure in prose: declarations dated 2026-08-09 that later work made false, in a
document nothing re-reads. A responsible-tech declaration that describes a project's controls
incorrectly is worse than no declaration, because it is the artifact a reader trusts instead of
checking. All three were contradicted by this repository's own README and CHANGELOG on the day
they were found.

1. **"The project has no deployed surface, no users other than the maintainer."** The site has
   been public since 2026-08-09 — the 2026-08-15 appendix says so three screens further down the
   same file. Corrected: there is a public, static, unauthenticated site with no accounts, no
   telemetry, and no analytics of any kind; "no users other than the maintainer" was never a
   privacy control and is not claimed as one.
2. **"The current fetcher does not autonomously retrieve or enforce `robots.txt`."** False since
   `src/mrf_honest/politeness.py` landed. `robots.txt` is fetched before the first request and
   obeyed with no override flag, an unreachable `robots.txt` is a complete disallow (RFC 9309
   section 2.3.1.4), a per-host minimum interval is held across a whole run and a `Crawl-delay`
   can only lengthen it, and `Retry-After` on 429 and 503 outranks this tool's own backoff. The
   attached condition — broad scheduled collection must not launch until the gap is resolved —
   is discharged as to robots and pacing. It is **not** discharged as to scheduling: a scheduled
   job still needs a service/job tier declaration (`docs/ROADMAP.md`), and none exists.
3. **"SAST, secret scanning, and dependency-audit jobs remain open before publication."** All
   three ship: hosted CodeQL for Python and Actions and a checksum-pinned full-history gitleaks
   scan on push, PR and a weekly schedule (`.github/workflows/security.yml`), and
   `pip-audit --strict` with no ignore list over the exported lockfile in `make verify`.

Two accuracy defects found in the same sweep, both now re-derived by
`tests/test_published_claims.py` rather than dated by hand: the metrics ledger claimed the audit
covered 116 pinned distributions when the export has always carried 51, and `perf/baseline.json`
described a nine-page audit as ten. The first was wrong on the day it was written, not stale;
`uv.lock` is byte-identical to that commit.

**Section A, restated for the published grade.** One more thing belongs here because it is an
ethics defect and not only a rendering one. A published file page carrying a hospital's name
stated that no warehouse contract evidence existed for its file and gave no reason, when the
reason was this project's own v3-only lakehouse refusing a file that declares template `2.0.0`.
`docs/how-we-compare.md` already required the reason for a project limit to be stated; the
pipeline discarded it before the renderer could. Refusals now carry their reason into the
comparison and onto the page. The general rule this makes explicit: **wherever a page shows the
absence of a check next to an organization's name, the absence needs a stated cause, because a
reader will otherwise supply one.**

**What is still open, unchanged by any of the above:** the manual screen-reader pass, the phase-4
retention period for discovery contact evidence, the bias review re-run when the cohort grows
past four large health systems, and the phase-5 "what this project got wrong" write-up.

## Appendix, 2026-08-19: the bias review re-run, now that the cohort has a sampling frame

The 2026-08-14 appendix closed with an obligation: *"Re-run this review when the cohort grows past
its current composition, and before any aggregate statistic about hospitals as a class is
published."* The cohort has grown from 6 files to 17, and — more to the point — it has acquired a
stated sampling frame ([docs/SAMPLING-FRAME.md](SAMPLING-FRAME.md)). This is that re-run.

**What changed about composition.** The first cohort was four large academic health systems, all
in two states, all well resourced. The 2026-08-19 cohort adds a stratum drawn at random, with a
committed seed, from CMS's own enumeration of 3,024 acute-care, non-federal hospitals: 48
facilities across 29 states. The drawn facilities include 10 for-profit (`Proprietary`) hospitals,
4 church-affiliated non-profits, 9 government hospitals (district, local, and state), and 2
physician-owned. The smallest graded file in the cohort is 6.7 MB and the largest is 884 MB — two
orders of magnitude, where the first cohort spanned one.

**The hazard the first review looked for did not reappear, and a different one did.** The 2026-08-14
review predicted the resource-shaped hazard would fall on small publishers and observed it falling
on a large one. In the random stratum it fell on neither. What the grade policy actually penalised
in a small hospital was a *stale file*: NMC Health, a community hospital in Newton, Kansas,
publishes a conforming CMS v3 document whose own `last_updated_on` is 2025-06-30, more than a year
before the assessment date. That is a **B**, driven by a `WARNING`, and it is the correct outcome:
the finding is about the document's declared date, not about the hospital's budget, and warnings
cap at B rather than failing anyone.

**The real bias in this cohort is not in the grade policy. It is in the profile.** 32 of the 48
drawn facilities — two thirds — publish their standard charges in a format this project does not
read: CSV, ZIP, or a vendor endpoint that answers `text/csv`. They are not graded, and they are
not failures; they are recorded exclusions with the reason stated. But the consequence for the
published grade distribution is systematic and must be named: **the cohort's grades describe the
subset of hospitals that chose JSON**, and format choice is not random with respect to size,
vendor, or engineering capacity. A reader who takes the letter distribution as a picture of US
hospital price transparency is reading a picture of JSON publishers. The site now states the frame
and the format rule on its methods page for exactly this reason.

**A second bias enters through origin resolution.** Neither CMS nor any other public dataset this
project found records which website a hospital selected to host its file, so a person has to
resolve each drawn facility to an origin. That step is not reproducible the way the draw is, and
it is systematically harder for small and vendor-hosted hospitals, whose files sit on
`cdn.`/`apps.`/`estimator.` subdomains that no naming convention predicts. Ten first-pass
candidate origins in this run were simply wrong, and had they not been re-checked, ten hospitals
would have been published as having failed to publish. The guard is stated in the frame document:
a returned `cms-hpt.txt` counts as the right origin only when one of its location entries names
the drawn facility, and a failed candidate is re-checked before the failure is recorded.

**What this review still cannot establish.** 48 facilities out of 3,024 supports no national rate,
and the cohort's two strata must not be pooled — the carry-forward stratum was chosen because it
was known, and averaging it with a probability sample produces a number that describes neither. No
aggregate statistic about hospitals as a class is published from this cohort, and the summary
counts on the site are labelled as descriptions of the cohort.

**Re-run this review** before any CSV profile ships (which would change the composition
fundamentally), before the sample is extended past 48, and before any proportion is published as
an estimate rather than a count.
