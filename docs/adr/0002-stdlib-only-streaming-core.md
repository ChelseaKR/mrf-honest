# 0002. Standard-library-only streaming core with a bytes-returning scan API

## Status

Accepted - 2026-08-07 (records decisions made 2026-08-04, when phase 1 landed; written down as
part of the standards conformance pass)

## Context

Phase 0 measured naive `json.load` on a 65 MB hospital file at 506 MB peak RSS (7.8x the file),
and payer files run one to three orders of magnitude larger. The streaming reader is therefore
the project's load-bearing engineering claim, and its memory behaviour is the number the
credibility rests on (`docs/PHASE-0-FINDINGS.md`).

During phase 1, the first working reader corrupted exactly one item per buffer refill while
every test passed: `_scan_value` captured absolute start/end indices into a buffer that a refill
could compact underneath them.

## Decision

1. The streaming core (`src/mrf_honest/stream.py`) uses the standard library only. A dependency
   that hides the memory behaviour would defeat the point of measuring it.
2. `_scan_value` returns the value's bytes, never a span of buffer indices, so the
   stale-index-after-refill bug cannot be expressed by the API at all. The regression test
   forces a 512-byte chunk size, because the defect only appears at refill boundaries and a
   realistic fixture would hide it.

## Consequences

- No `ijson`/`orjson`-style dependencies in the ingestion path; runtime dependencies are empty
  (`[project] dependencies = []`) and stay that way for this layer.
- Peak-RSS measurements published in the docs are attributable to code in this repo, not to a
  third-party parser's internals.
- The buffer API trades a small amount of copying for structural immunity to index invalidation.
  The original result was 27 MB peak on the 65 MB file (0.42x); after parser hardening, the
  final 2026-08-09 gate is 33,865,728 bytes on the 64,828,148-byte file (0.5224x). Both
  measurements and their context remain in `docs/PHASE-0-FINDINGS.md`.
