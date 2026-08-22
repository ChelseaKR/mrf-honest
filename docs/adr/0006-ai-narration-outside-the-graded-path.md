# 0006. AI narration outside the graded path, with citations verified against a committed corpus

## Status

Accepted - 2026-08-21

## Context

Every grade this project publishes is deterministic, and the house rule behind that is written
down twice: "Deterministic core; no model anywhere in the grading or comparison path"
(`docs/IMPLEMENTATION-PLAN.md`, "Engineering standards, inherited") and "AI evaluation: N/A, no LLM
or model component" (`docs/RESPONSIBLE-TECH-AUDITS.md`). Those sentences protect the property that
makes a grade worth anything: same input, same grade, and a finding table a reader can re-derive.

They also leave a gap the grade itself cannot close. A finding row says `INTERPRETABILITY_ALGORITHM_RATES`
with a message and two URLs. A reader who is not a data engineer still has to open 45 CFR
§ 180.50 and a CMS data dictionary and work out what that row means for the file in front of them.
`docs/how-we-grade.md` helps, at the catalog level; it cannot explain one hospital's file.

A language model can write that explanation. Left alone it will also invent section numbers,
deadlines, and requirements, and it will say "compliant" or "noncompliant" when asked not to.
The project's zero-fabrication rule (`docs/CONTEXT.md`) applies to prose as much as to counts.

The portfolio's `permit-bearings` repository worked out, the same day, a shape that holds the
line: the model narrates, the committed source text is the evidence, and a program verifies every
quoted citation before a sentence is shown. This ADR adopts that shape here, written fresh for
this codebase and its catalog.

## Decision

Add an optional narration layer, `mrf_honest.ai`, that explains one already-graded assessment
record in plain language and is kept outside the graded path by construction:

- **Inputs only.** The layer reads a `FileAssessment` record and calls `grade_assessment` on it; it
  never calls inspection, scoring, or comparison, and nothing in those modules, the CLI's other
  commands, or the site renderer imports it. The deterministic-core rule is unchanged in meaning
  and is now stated with this boundary named.
- **A committed corpus of the cited texts.** `corpus/SOURCES.json` maps the citation URLs the
  finding catalogs already use to retained copies: 45 CFR Part 180 as eCFR XML (point in time
  recorded), and the CMS JSON and CSV data dictionaries as Markdown, each with URL, retrieval
  date, SHA-256, and the reason any cited document is not retained (the CMS policy FAQ PDF).
  These are works of the United States Government, reproduced unmodified.
- **Claims must quote, and quotes must verify.** The model is shown only passages from the
  documents the record's own findings cite, ranked lexically by finding code, message, and
  catalog description. Every claim must cite passage IDs with verbatim quotes; the layer checks
  each quote against the whole retained document after typography, case, and whitespace folding,
  with a minimum length. A claim with any citation that does not verify is withheld and counted.
  A record that offers no passage at all — no findings, or findings citing only documents the
  corpus does not retain — is refused before the model is called, with the reason recorded in
  the narration's provenance, because every claim would be withheld and the call would spend
  tokens to say nothing (#26).
- **Labeled, and honest about what verification means.** Output carries a label stating that the
  grade and findings were computed without a model, that a verified citation proves the passage
  exists and says those words, not that the sentence is a correct reading of the regulation, and
  that this is not legal advice or a compliance determination. Prompt version and model are
  recorded with every output.
- **Provider through the public SDK, credential from the environment.** Anthropic API or Amazon
  Bedrock via the `anthropic` package, in an optional `ai` extra; the core keeps its
  standard-library-only boundary (ADR 0002) because nothing on the graded path imports the extra.
- **Measured, not asserted.** `python -m mrf_honest.ai.eval` runs the narration over committed
  assessment records and reports claims generated, shown, and withheld, with provider, model,
  prompt version, date, and commit recorded; a test refuses a result file without that provenance.
- **A CLI, not a site feature.** `mrf-honest narrate` prints the narration. The published site
  keeps its zero-script resource budget and shows no AI prose; putting narration on a public page
  would need its own ADR covering review, labeling, and cost.

## Consequences

- `README.md` and `docs/RESPONSIBLE-TECH-AUDITS.md` stop saying "no LLM or model component". They
  now say the grading and comparison path has none, and that an optional narration layer exists
  outside it with the controls above. `docs/IMPLEMENTATION-PLAN.md` keeps its rule and names the
  boundary.
- The locked dependency set grows by the `ai` extra; `docs/ROADMAP.md`'s audited count and the
  test that pins it move with it. `pip-audit --strict` covers the extra.
- A narration can be wrong while every citation verifies: it can quote a true passage in support
  of a mistaken sentence. The verifier bounds fabricated citations; it does not bound
  misreadings. No person has reviewed the prompt, the passages it selects, or the Spanish output.
- Narration is non-deterministic run to run. Grades are not.

## Alternatives considered

- **Keep the N/A.** Honest, and leaves the reader alone with the CFR. Rejected for the reader's
  sake, with the boundary above as the price.
- **Let the model summarize the file directly.** Rejected: that is a model in the graded path.
- **Pre-render narrations into the site.** Rejected for now: unreviewed AI prose on a public
  page that otherwise publishes only measured facts would need review and labeling rules this
  ADR does not establish, and the site's script budget is a deliberate zero.
