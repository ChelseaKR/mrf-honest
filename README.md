# mrf-honest

**Status: planning only. No code yet.** This repository currently holds the context and
implementation plan for a project to be built later. Nothing here is a claim about work done.

## The idea in one paragraph

US hospitals and health insurers are legally required to publish machine-readable files (MRFs) of
their prices: what hospitals charge, and what payers have negotiated to pay. The files are public,
mandated, and enormous, and they are also famously difficult to use, because technical compliance
and actual usability are different things. `mrf-honest` would ingest those files at real scale,
grade each publisher on whether their file is genuinely usable, and publish price comparisons with
honest statistics attached, including the uncertainty and the suppression that most price
comparisons quietly omit.

## Why this shape

Two reasons, and the second one is the honest one.

**The public-interest reason.** Price transparency rules produced files, and files are not
transparency. A hospital can publish a technically conforming document that no one can act on, and
nothing in the rule distinguishes that from a good-faith publication. Grading the difference is
useful and nobody is doing it in the open.

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
spec-cited, and the project publishes its own errors. A commercial product has a structural reason
to make its data look more complete and more comparable than it is. An open project has the
opposite incentive, and that is the whole value proposition.

## Planned deliverables

- A streaming ingestion pipeline that handles multi-gigabyte compressed JSON without loading it
- A local lakehouse (Parquet + DuckDB) with declared models and enforced data contracts
- A per-file quality scorecard, in the pattern of `gtfs-scorecard` and `fhir-scorecard`
- A published dataset with a documented schema, plus a static site and API
- Price comparisons that carry small-cell suppression and uncertainty intervals by construction

## Documents

| Document | What it covers |
|---|---|
| [docs/CONTEXT.md](docs/CONTEXT.md) | Why this project exists, what gaps it closes, when to build it |
| [docs/DATA-LANDSCAPE.md](docs/DATA-LANDSCAPE.md) | What MRFs actually are, the schemas, the scale, the known pitfalls |
| [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) | Phased build plan with decision points and stop conditions |

## Provenance

Personal open-source project, planned and to be built on personal time and equipment, unaffiliated
with any employer or client, past or present.

License: Apache-2.0 (intended).
