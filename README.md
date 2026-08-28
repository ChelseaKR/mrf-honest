# mrf-honest

**Deterministic, spec-cited grades for hospital price-transparency files, published with the
evidence attached.**

Two graded cohorts are live, one per CMS file format, side by side and never pooled. The JSON
cohort covers 17 machine-readable files across 15 publishers, discovered from
CMS-conventional `cms-hpt.txt` documents, retrieved in one identified run, streamed without
loading into memory, and graded fail-closed. The distribution is 12 **A**, 1 **B**, 2 **C**,
2 **F**, and 0 not graded. Every grade, count, and finding on the
[site](https://chelseakr.github.io/mrf-honest/) is generated from the committed comparison
documents ([JSON cohort](data/cohorts/2026-08-19.comparison.json),
[CSV cohort](data/cohorts/2026-08-19-csv.comparison.json)), never typed in, and each finding
cites the CMS rule ([45 CFR § 180.50]) or
[CMS schema documentation](https://github.com/CMSgov/hospital-price-transparency) it rests on.

**The cohorts have a stated sampling frame**, which the first one did not
([docs/SAMPLING-FRAME.md](docs/SAMPLING-FRAME.md)). Eleven of the seventeen JSON files come from
a seeded random draw of 48 facilities from CMS's own enumeration of 3,024 acute-care,
non-federal hospitals — 29 states, for-profit and church and county and academic. The other six
are every subject the first cohort published, carried forward rather than quietly dropped. All
48 drawn facilities were attempted, and the 37 that the JSON cohort did not grade are published
as recorded exclusions with the origin checked and the reason found. A cohort pruned of its
failures would grade better and describe less, which is the exact defect this project exists to
catch.

**The majority format is now graded, not excluded.** Two thirds of the randomly drawn
hospitals — 32 of 48 — publish their standard charges as CSV, ZIP, or a vendor endpoint that
answers `text/csv` rather than JSON; until 2026-08-19 every one was a recorded exclusion, and
the letter distribution above described hospitals that chose JSON, not hospitals. A second
assessment profile now implements CMS's CSV v3.0.0 templates, Tall and Wide, and a sibling
cohort grades all 25 CSV targets of the same draw. The CSV distribution is 11 **A**, 2 **B**,
4 **C**, 3 **D**, 1 **F**, and 4 not graded — two hosts whose `robots.txt` says no, honored;
two files over this project's own 1 GiB ceiling, stated rather than blamed on the publisher.
What remains outside both profiles stays recorded: 7 ZIP archives, 4 origins whose
`cms-hpt.txt` could not be retrieved, and 1 whose location entry did not resolve.

**The CSV profile's first real cohort produced its own best findings.** Across six files,
118,411 payer or plan names are encoded with no charge beside them — the CSV data dictionary's
first conditional requirement, violated at scale. The distribution of that number is itself the
finding: 118,096 of the instances sit in the two files still publishing the superseded v2.0.0
template more than seven months after CMS's v3.0.0 effective date — in one of them, every
single data row — while the four current-template files carry only a few-hundred-row residual.
That is the same defect class as the Cedars-Sinai finding below, measured now in CSV; a third
hospital declares template `3.0.1`, a version CMS never published. One hospital's own `cms-hpt.txt` points at a URL that
answers HTTP 404; that is the CSV cohort's F, stated with the dated reason. A single file
carries 4,785 methodology values outside the CMS accepted set; 3 files are not valid UTF-8 and
were read as Latin-1 with the tolerance recorded, and 8 of the 25 begin with a UTF-8
byte-order mark.

The two **F**s are retrieval failures at the URLs the hospitals' own `cms-hpt.txt` documents
publish — Northside Hospital Duluth's answers HTTP 403 to an identified client, and Rio Grande
Regional Hospital's answers HTTP 409, *"Public access is not permitted on this storage account."*
Both are stated with the dated reason rather than dropped. The two **C**s are both version
strings: an 884 MB Cedars-Sinai file that declares the superseded 2.0.0 template seven months
after CMS's v3.0.0 effective date while carrying, element for element, the v3.0.0 envelope that
version string says it does not have ([the finding, with
evidence](docs/findings/superseded-template-version-2026-08-14.md)), and Central Maine Medical
Center's, which declares `3.0` where CMS specifies `3.0.0`. The one **B** is a conforming file
whose own `last_updated_on` is more than a year before the assessment date. 5 of the 17 files
begin with a UTF-8 byte-order mark that RFC 8259 forbids and strict JSON parsers reject; the
catalog records it as a tolerated `INFO` observation, and all five grade **A**.

The first real cohort also broke the pipeline twice, and both breaks are published: a CSV
dialect the spool reader guessed instead of declared (fixed, regression-pinned), and a default
memory ceiling the two largest exports exceeded (an operator setting, documented). Finding that
out on six files instead of six hundred is the point of grading a small cohort first.

## The idea in one paragraph

US hospitals and health insurers are legally required to publish machine-readable files (MRFs)
of their prices: what hospitals charge, and what payers have negotiated to pay. The files are
public, mandated, and enormous, and they are also famously difficult to use, because technical
compliance and actual usability are different things. `mrf-honest` ingests those files at real
scale, grades each published *file* on whether it is genuinely usable, and publishes the method,
the evidence, and its own mistakes alongside the grades. Rate comparisons are deliberately not
published until the suppression and uncertainty work exists to publish them honestly.

## Quickstart

```sh
uv sync
make verify

# Inspect a local CMS hospital JSON v3 file. Findings are observations, not a compliance ruling.
uv run mrf-honest inspect prices.json --as-of 2026-08-09 --format json

# Inspect a local CMS hospital CSV v3 file (Tall or Wide) under the CSV profile.
uv run mrf-honest inspect standardcharges.csv --profile csv --as-of 2026-08-09 --format json

# Classify what a URL serves with one bounded ranged request (~4 KB), before deciding which
# profile to grade it under. Never a grading input; robots.txt is consulted first, no override.
uv run mrf-honest probe https://files.example.org/standardcharges \
  --contact operator@example.org

# Build a contracted local snapshot. DuckDB is supplied by the dev group or the lakehouse extra.
uv run mrf-honest ingest prices.json \
  --publisher-id example-health \
  --warehouse warehouse \
  --as-of 2026-08-09 \
  --format json

# Retrieve one file and atomically retain its remote-plus-local scorecard.
uv run mrf-honest scorecard https://files.example.org/standardcharges.json \
  --publisher-id example-health \
  --publisher-type hospital \
  --location-id main-campus \
  --url-provenance cms_hpt \
  --registry data/scorecards.jsonl \
  --cache-dir data/cache \
  --contact operator@example.org \
  --format json

# Turn one attested collection run into the published comparison, then render the site.
# Every ingest attempt contributes one evidence document, whether the warehouse loaded the file
# or refused it; a refusal carries the reason, which the file page publishes.
uv run mrf-honest compare \
  --assessments data/cohorts/2026-08-19.assessments.jsonl \
  --manifest data/cohorts/2026-08-19.json \
  $(for e in data/cohorts/2026-08-19.ingest/*.json; do printf ' --ingest-result %s' "$e"; done) \
  > comparison.json
uv run mrf-honest site --comparison comparison.json --out site
```

Re-running that command over the committed inputs reproduces
[the committed comparison](data/cohorts/2026-08-19.comparison.json) byte for byte, and both
`make verify` and the publish workflow check exactly that before anything is rendered from it.
Every published cohort is checked that way, not just the newest one.

The CLI also provides `discover`, `fetch`, `probe`, `profile`, `explain`, and `narrate`; `grade`
is an alias for `scorecard`, and `scorecard --profile csv` assesses a CSV publication under the
CSV profile. `narrate` is the one command that calls a model: with the `ai` extra installed and
`MRF_AI_PROVIDER`/`MRF_AI_MODEL` set (Anthropic API or Amazon Bedrock through the public SDK), it
explains one already-graded record in English or Spanish, and every sentence it prints quotes a
passage verified against `corpus/`; it never changes a grade ([ADR 0006](docs/adr/0006-ai-narration-outside-the-graded-path.md)). Retrieval requires an identifying contact string, caches decoded content by
SHA-256, validates HTTPS redirects, applies size limits and retry backoff, checks the body that
arrived against the `Content-Length` the server declared so a transfer that stopped early is
never inspected as though it were the whole document, and records discovery
attempts—including failures—in append-only JSONL evidence.

## What a grade is, and is not

A letter grade here describes **one published file under one stated, fingerprinted policy on
one date** ([docs/how-we-compare.md](docs/how-we-compare.md), [ADR 0005](docs/adr/0005-presentation-grade-layer.md)).
It never ranks hospitals, never prices care, and never determines compliance with
[45 CFR § 180.50] or any other law. The grade is fail-closed in both directions: a file the
public cannot download is a stated **F** with the dated reason, and a target this project's own
limits prevented assessing is **not graded** — stated, never silently dropped, and never
conflated with a publisher failure. That boundary is enforced by a status matrix rather than by
care: a certificate that will not verify, a `robots.txt` that says no, and this project's own
size ceiling are all **not graded**, because from one attempt none of them is distinguishable
from a problem on this end. An **A** means the implemented checks emitted nothing; it is
not the official CMS validator and not a certificate of validity.

## Why this shape

Two reasons, and the second one is the honest one.

**The public-interest reason.** Price transparency rules produced files, and files are not
transparency. A hospital can publish a technically conforming document that no one can act on,
and nothing in the rule distinguishes that from a good-faith publication. Grading the difference
is useful and nobody is doing it in the open.

**The portfolio reason.** This closes two specific gaps in the author's record, documented in
[docs/CONTEXT.md](docs/CONTEXT.md): modern data-platform engineering (lakehouse, declarative
modeling, data contracts, warehouse-scale cost thinking) and payer/claims economics. Those gaps
have cost real opportunities. This project is designed to close them with a working artifact
rather than a claim.

## What would make it different from what already exists

Commercial products already parse MRFs. Turquoise Health, Serif Health, and Payless Health all
work in this space and several are well funded. **This is not a first mover and the plan should
never pretend otherwise.**

The differentiator is the same one that runs through the rest of this portfolio: the methodology
is public, the statistics are honest about uncertainty, the quality grading is deterministic and
spec-cited, and the project publishes its own errors. A commercial product has a structural
reason to make its data look more complete and more comparable than it is. An open project has
the opposite incentive, and that is the whole value proposition.

## What exists today, and what remains

Built:

- A standard-library streaming JSON reader with bounded problem samples and peak memory tied to
  one item rather than the whole file ([ADR 0002](docs/adr/0002-stdlib-only-streaming-core.md));
  the phase-0 measurements are in [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md). The
  largest file in the current cohort is 883,973,507 bytes and streams to completion.
- Five-field, multi-location `cms-hpt.txt` discovery, an identified conditional fetcher,
  content-addressed cache, and a v2 append-only registry that retains dated success and failure
  evidence.
- A deterministic five-dimension file assessment — retrievability, conformance, completeness,
  interpretability, freshness — with a source-cited finding catalog
  ([docs/how-we-grade.md](docs/how-we-grade.md)) and integrity-hashed persisted records
  ([ADR 0004](docs/adr/0004-separate-remote-scorecard-artifacts.md)).
- A second assessment profile for CMS's CSV v3.0.0 templates, Tall and Wide
  (`src/mrf_honest/inspect_csv.py`): general data elements matched by name rather than
  position, the dictionary's twelve conditional requirements, accepted-value sets, placeholder
  detection, and the same five dimensions, streamed row by row with bounded memory — a 319 MB,
  1.5-million-row file inspects in about 21 seconds. Each profile carries its own policy
  fingerprints, and the comparison layer refuses to pool them.
- A bounded format probe (`mrf-honest probe`): one ranged, identified, robots-checked GET of
  ~4 KB that classifies what a URL serves by its leading bytes — ZIP magic, a JSON opener, an
  HTML doctype, or the CMS CSV header row — so routing a target to a profile no longer costs a
  full download. The 2026-08-19 run had spent 669,479,338 bytes learning four extensionless
  targets were CSV; the probe answers that in kilobytes, and is never a grading input.
- A DuckDB + partitioned-Parquet lakehouse with 13 documented models, executable data contracts
  at every layer boundary, exact raw text retention, `DECIMAL(38,10)` numerics, and idempotent
  content-addressed run identity ([docs/MODEL-DAG.md](docs/MODEL-DAG.md),
  [ADR 0003](docs/adr/0003-local-lakehouse-duckdb-parquet.md)). 13 of the cohort files are
  contracted through it; two declare a template version the v3-only pipeline does not implement
  (`2.0.0`, and `3.0` where CMS specifies `3.0.0`) and it refuses them, and two were never
  retrieved at all. Each refusal is recorded as evidence with its reason and published on the
  file's page, because a limit of this project rendered as a bare absence reads like an unnamed
  defect in a named hospital's file.
- `robots.txt`, per-host pacing and `Retry-After` enforced in the fetcher rather than by an
  operator's habits (`src/mrf_honest/politeness.py`). robots is fetched before the first request
  and obeyed with no override flag; an unreachable `robots.txt` is a complete disallow per
  RFC 9309 section 2.3.1.4 and a 4xx means none exists per 2.3.1.3; a per-host minimum interval
  is held across a whole run and a `Crawl-delay` can only lengthen it; `Retry-After` on 429 and
  503 outranks this tool's own backoff. A robots disallow is **not graded**, never an F: it is a
  fact about this crawler's permission, not about whether the hospital published. Measured by a
  localhost-server suite in `tests/test_politeness.py`.
- A comparison layer (`mrf-honest compare`) that turns one attested collection run into a
  published comparison under a versioned, fingerprinted grade policy, refusing mixed scopes,
  unattested runs, and duplicate subjects ([docs/how-we-compare.md](docs/how-we-compare.md)).
- A dependency-free static site (`mrf-honest site`) with one indexable page per graded file,
  spec citations on every finding, verification provenance down to the content SHA-256, and a
  fail-closed coverage statement, deployed by a SHA-pinned Pages workflow that rebuilds only
  from committed data.

Still open:

- structural separation of dollar, percentage, and algorithm representations exists, and so now
  do small-cell suppression and uncertainty intervals (ADR 0007), but they are applied to the
  disposition of a drawn sample rather than to prices: **no price comparison is published
  anywhere**, and none will be until a rate comparison can carry its own uncertainty;
- a payer-MRF pipeline, ZIP-container handling, and warehouse (lakehouse) support for the CSV
  profile: the warehouse remains JSON-v3-only, so CSV cohort pages state that no contract
  evidence exists rather than implying a pass (a `.zip` publication is still recorded and
  excluded rather than mis-graded);
- safe concurrent-writer coordination, supported warehouse migrations, and a full SIGKILL/fsync
  crash matrix; and
- the phase-5 dataset export, API, MCP server, and release process.

## Documents

| Document | What it covers |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | Why this project exists, what gaps it closes, when to build it |
| [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md) | What MRFs actually are, the schemas, the scale, the known pitfalls |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | Phased build plan with decision points and stop conditions |
| [docs/EXPANSION-PLAN.md](docs/EXPANSION-PLAN.md) | Phases 6 through 14: what is built next, what it depends on, and what only a person can do |
| [docs/PHASE-0-FINDINGS.md](docs/PHASE-0-FINDINGS.md) | Measured phase-0 constraint study and the phase-1 streaming result |
| [docs/PHASE-2-FINDINGS.md](docs/PHASE-2-FINDINGS.md) | Real-file lakehouse acceptance, counts, storage, and limits |
| [docs/PHASE-3-FINDINGS.md](docs/PHASE-3-FINDINGS.md) | Fail-closed remote scorecard contract, verification, and limits |
| [docs/MODEL-DAG.md](docs/MODEL-DAG.md) | Model grains, lineage, contracts, and methodology-safe query |
| [docs/how-we-grade.md](docs/how-we-grade.md) | Assessment semantics and the source-cited finding catalog |
| [docs/how-we-compare.md](docs/how-we-compare.md) | The comparison boundary and the published file-grade policy |
| [docs/findings/](docs/findings/) | Written-up findings from published cohorts, with evidence |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Current position, observability declaration, metrics ledger |
| [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) | Responsible-tech declarations for the current scope |
| [docs/adr/0006-ai-narration-outside-the-graded-path.md](docs/adr/0006-ai-narration-outside-the-graded-path.md) | Why a model may narrate a finished grade, and the verifier that keeps it honest |
| [corpus/SOURCES.json](corpus/SOURCES.json) | The retained copies of the texts the findings cite, with hashes and retrieval dates |

## Standards Conformance

Per the portfolio's standards set. N/A rows carry a reason, and the judgment-call ones cite an
ADR in [docs/adr/](docs/adr/). No blank rows, no silent skips.

| Standard | State |
|---|---|
| Code Quality | Applies: `make verify` runs six gates — `ruff check` (security `S` rules, `max-complexity=10`), `ruff format --check`, `mypy --strict`, pytest with a branch-coverage floor of 85, `uv lock --check`, and `pip-audit --strict` over the exported lockfile. Current: 445 tests, 92.44% branch coverage, zero lint/format/type findings, lockfile in sync, zero known vulnerabilities (2026-08-21). Floors: Python >= 3.12 (`.python-version` pins 3.14), ruff >= 0.15, mypy >= 1.18, locked in `uv.lock`. Dev tooling is a PEP 735 `[dependency-groups]` group, so `uv sync` installs it and a published wheel never carries it. |
| Security & Supply-Chain | Applies: the streaming, inspection, discovery, fetch, registry, comparison, and site path is standard-library-only; DuckDB is an optional lakehouse dependency ([ADRs 0002-0003](docs/adr/)) and the `anthropic` SDK an optional `ai` extra that only the narration layer imports ([ADR 0006](docs/adr/0006-ai-narration-outside-the-graded-path.md)). The lockfile, ruff `S` gate, HTTPS/redirect validation, bounded downloads, and SHA-pinned CI actions reduce the current surface. Hosted CodeQL (Python and Actions) and a checksum-pinned full-history gitleaks scan run on push, PR, and weekly schedule (`.github/workflows/security.yml`). `make verify` runs `pip-audit --strict` against the whole exported lockfile — every extra and the dev group — with no ignore list, so the audit runs on a laptop and in CI rather than only in CI. The lockfile-drift gate is `uv lock --check`, not `uv sync --frozen`: measured on a deliberately drifted project under uv 0.12.1, `uv lock --check` and `uv sync --locked` exit 1 and `uv sync --frozen` exits 0, because `--frozen` installs from the lockfile without reading `pyproject.toml` and so cannot see the two disagree. |
| CI/CD | Applies: SHA-pinned workflows mirror `make verify` on Python 3.12 and 3.14, build distributions, and publish the site from committed data only. The publish job first re-derives the newest comparison from its committed assessments, manifest, and ingest evidence and requires a byte-for-byte match, then requires one rendered page per row in it; a generator that no longer reproduces its own published artifact cannot deploy. `make verify` runs the same derivation, but that is a separate workflow whose failure would not by itself stop a deploy, which is why the check is on both paths. |
| Observability | Applies to the local batch shape plus a static published artifact: finalized run manifests and DuckDB `model_metric` rows retain counts, bytes, and wall time; the site is rebuilt from committed data with no availability objective declared. See [docs/ROADMAP.md](docs/ROADMAP.md). |
| Accessibility | Applies as of the site, and now gated. `.github/workflows/accessibility.yml` runs Lighthouse over **every** page the render produced — enumerated from the build, not typed into the workflow — and requires 1.0 on accessibility, best-practices and SEO, a declared floor above the standard's 0.90. `make verify` runs the parts that need no browser: a contrast assertion over every declared text/background pair in the design tokens, and a heading-order check on every generated page. Two real defects were found and fixed when the gate was first pointed at the live site (`heading-order` on the index; a 4.28:1 finding chip on every file page with a warning). The remaining open obligation is the manual screen-reader pass, stated in [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md). |
| Internationalization | Applies: the site and CLI are English-only by a recorded decision with its limits stated: [docs/I18N.md](docs/I18N.md). |
| AI Evaluation | Applies to the optional narration layer only ([ADR 0006](docs/adr/0006-ai-narration-outside-the-graded-path.md)): the grading and comparison path has no LLM or model component and never will by written rule ([docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)); `mrf-honest narrate` explains an already-graded record with claims that must quote the committed copies of the texts the findings cite (`corpus/`), and a claim whose quote does not verify is withheld. `python -m mrf_honest.ai.eval` measures that; the recorded runs are in `evals/ai/results/` with provider, model, prompt version, date, and commit. A verified citation proves the passage exists, not that the sentence reads it correctly; no person has reviewed the prompt or the Spanish output. |
| Quality & Metrics | Applies: metrics ledger in [docs/ROADMAP.md](docs/ROADMAP.md); every published number is measured or generated from committed data, never estimated. |
| AI Development Measurement | Applies: this project is built AI-assisted and says so (see Provenance below). The outcome side is the metrics ledger in [docs/ROADMAP.md](docs/ROADMAP.md), where every published number is measured or generated from committed data rather than estimated. The diagnostic counters the standard names — sessions, tokens, share of generated code, acceptance rate — are not instrumented in this repository, and by the standard's own rule they would be observe-only if they were: they never gate a merge and they never rank a person. |
| Documentation | Applies: README, `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CITATION.cff`, ADR log ([docs/adr/](docs/adr/)), findings log ([docs/findings/](docs/findings/)). |
| Incident Response | Applies: [SECURITY.md](SECURITY.md) names a private reporting channel, dated response targets (acknowledgement within 72 hours, triage and severity within 7 days), and the one operational incident this project can actually have — a fetched file found to contain individual-level data, which is reported to the publisher rather than analyzed. There is no deployed service and no on-call rotation; a committed postmortem template and an incident-label convention are not yet in the repository. |
| Data Governance | Applies: every input is a file its publisher is legally required to post publicly, and it carries prices, not patients. Retrieval records the source URL, provenance tag, fetch time, byte count, and content SHA-256, so a published grade traces back to the exact bytes it graded and the comparison is re-derivable from committed assessments, manifest, and ingest evidence. The datasets and their schemas are documented in [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md), and the handling rule for a file that turns out to contain individual-level data is in [SECURITY.md](SECURITY.md). A standalone data card and a written retention policy for the local blob cache are not yet in the repository. |
| Responsible-Tech Framework | Applies: [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md) (grades files, never organizations or care; dated appendices for the grade-bias review, the site's accessibility scope, and a 2026-08-16 sweep that found three declarations the later work had made false and left published). |
| Performance | Applies as of the site. The same Lighthouse job asserts a performance floor and a resource budget in which every non-document resource type is zero: no scripts, no external stylesheets, no fonts, no images, no third parties. Measured 2026-08-15 across all nine pages: 1.0 performance, 12,197 bytes and one request on the heaviest page ([perf/baseline.json](perf/baseline.json)). The k6 latency rows of the performance standard are N/A: there is no server, only static files, and that reason is recorded in the baseline. |
| Release & Versioning | N/A (pre-publication as a package: no tags, no downstream consumers; the site is a continuously rebuilt artifact). [docs/adr/0001-release-versioning-na.md](docs/adr/0001-release-versioning-na.md). |

## Provenance

Personal open-source project, planned and built on personal time and equipment, unaffiliated with
any employer or client, past or present.

Built AI-assisted (Claude Code and OpenAI Codex). Every number in the docs and this README was
measured or generated from committed data, never invented; the maintainer reviews and owns every
line.

License: Apache-2.0.

[45 CFR § 180.50]: https://www.ecfr.gov/current/title-45/subtitle-A/subchapter-E/part-180/subpart-B/section-180.50
