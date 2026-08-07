# 0001. RELEASE-AND-VERSIONING-STANDARD: declare N/A (pre-publication, not consumed downstream)

## Status

Accepted - 2026-08-07

## Context

The portfolio's RELEASE-AND-VERSIONING-STANDARD requires every repo to either produce releases
(tags, a CHANGELOG-driven version bump, a release workflow) or explicitly declare N/A with a
reason. mrf-honest has zero git tags, no remote, no release workflow, and no consumers: nothing
outside this repository pins to a version or artifact of it. The package version is
`0.1.0.dev0` and is not published anywhere.

The implementation plan (docs/IMPLEMENTATION-PLAN.md, phase 5) explicitly schedules
"CITATION.cff and dated releases" and a CI pipeline as deliverables of the publish phase. The
project is at phase 1.

## Decision

Declare Release & Versioning N/A (pre-publication, not consumed downstream). No release process
exists yet and none is claimed. `CHANGELOG.md` is still kept (the documentation standard does
not allow marking the changelog itself N/A); its entries are dated, not versioned, until the
first release.

## Consequences

- README's Standards Conformance table carries
  `Release & Versioning | N/A (pre-publication, not consumed downstream)` citing this ADR.
- No version-bump or tag process is required for day-to-day work.

## Revisit if

Phase 5 begins (publishing the dataset, site, or package), the repository gains a public remote,
or anything external starts consuming this code. At that point dated releases, tags, and a
release workflow become required and this ADR is superseded.
