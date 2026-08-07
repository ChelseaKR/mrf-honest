# 0000. Record architecture decisions

## Status

Accepted - 2026-08-07

## Context

The portfolio's CODE-QUALITY-STANDARD requires an ADR for any choice that is expensive to
reverse, including declaring a portfolio standard N/A. Until this conformance pass, mrf-honest
had no `docs/adr/` at all, even though it had already made at least two such choices (a
standard-library-only streaming core, and shipping without releases).

## Decision

Use a lightweight MADR-style format: one file per decision, numbered sequentially
(`NNNN-kebab-case-title.md`), append-only. Superseding an old decision adds a new ADR that says
so; it does not edit or delete the old one. Each ADR carries Status, Context, Decision, and
Consequences, and, where it backs a standards N/A declaration, an explicit "Revisit if" trigger.

## Consequences

- Every N/A row in the README's Standards Conformance table that rests on a judgment call must
  cite an ADR number.
- ADRs are the durable record of why, so a future maintainer does not have to reconstruct the
  reasoning behind a decision from git blame.
