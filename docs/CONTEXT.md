# Context: why this project, and when to build it

Written 2026-08-05, during an active job search, so future-me remembers the reasoning rather than
just the plan.

## The gap this closes

A portfolio audit against the live application pipeline found two recurring requirements with no
supporting artifact:

**Modern data platform.** Warehouse or lakehouse architecture, declarative modeling (dbt-shaped),
data contracts, query cost management, lineage. The existing record has real ETL and real
production pipelines, including a weekly catalog operation held accurate for three-plus years and
a nightly job rebuilt to run four times daily after it was found silently dropping records. That
is pipeline engineering, and it is genuinely good, but it is not the same thing as owning a
lakehouse, and interviewers in that lane can tell the difference in about two questions.

**Payer and claims economics.** Negotiated rates, price transparency, the commercial mechanics of
what care costs and who pays. The health record is Medicaid and CHIP reporting, drug rebate
program oversight, and FHIR interoperability. All payer-adjacent, none of it pricing.

## What it already cost

Concrete example from 2026-08-05: **Unite Us, Senior Manager, Data Engineering**, $190-215K,
outstanding mission fit (social care coordination, exactly this author's domain). The requirements
were deep hands-on Snowflake with performance tuning and cost management, production Airflow with
DAG design and backfills, and data lakehouse design. Those are specific and checkable, and the
honest answer was no. The role was deprioritized.

That is the pattern to fix: the *domain* fit was better than almost anything else in the pipeline
and the *stack* fit was the weakest. Roles at the intersection of health data and data platform
are common, and this gap disqualifies from all of them.

## What building it would let me say

Only what is true, and after the work exists:

> Built an open pipeline that ingests multi-gigabyte payer and hospital price-transparency files,
> models them in a Parquet and DuckDB lakehouse under enforced data contracts, grades each
> publisher's file on usability, and publishes comparisons with small-cell suppression and
> uncertainty intervals.

That sentence answers the Snowflake-and-Airflow question with something better than "no": a
working system at real data volume, with the cost and correctness tradeoffs made explicitly.
DuckDB and Parquet are not Snowflake, and the plan should never imply they are, but "I built the
lakehouse and the contracts and can explain every modeling decision" travels further than a tool
name on a skills list.

## When to build it

**Not while the pipeline is hot.** As of 2026-08-05 there were five strong packages in flight
(Prescryptive, Counterpart Health, Propel, Cohere Health, Talkiatry) and the binding constraint
was submissions and follow-through, not portfolio depth. `fhir-scorecard` already covers the
health-AI and interoperability lane, which is where the pipeline actually is.

Build this when one of these is true:

1. The search shifts toward Director of Data or data-platform leadership as the primary lane
2. The pipeline goes quiet and there is a block of uninterrupted time
3. An interview process makes the gap concrete, for example a take-home or a system-design round
   on data architecture

**Estimated effort: two to four weeks part-time** for a defensible v0.1, which is materially more
than `fhir-scorecard` took, because the data volume is real. Do not start it expecting a weekend.

## Constraints that carry over from the rest of the portfolio

- **Zero fabrication.** Publisher counts, file sizes, and coverage numbers get measured, never
  estimated into a README.
- **Statistical honesty is the product.** Suppression and uncertainty are not caveats bolted on at
  the end; if the design does not carry them structurally it is not this project.
- **Publish the errors.** `fhir-scorecard` documents five of its own measurement mistakes and that
  section is the most valuable thing in the repository. Same discipline here.
- **No scraping what is not offered.** These are files publishers are legally required to post.
  Fetch politely, honor robots and rate limits, identify the client, and stop if asked.
- **No intended PHI.** The required MRF schemas do not call for patient data, and MRFs should not
  contain it. If a file contains individual-level data, that is a disclosure incident to report to
  the publisher, not a dataset to analyze.
