# 0003. Local lakehouse with DuckDB, partitioned Parquet, and source-scoped keys

## Status

Accepted - 2026-08-09

## Context

Phase 2 needs to demonstrate lakehouse decisions, data contracts, lineage, and cost signals while
remaining runnable on a laptop. The streaming core stays standard-library-only under ADR 0002;
hand-writing a Parquet encoder or query engine would add risk without strengthening that claim.

CMS hospital JSON v3 is nested at item, code, standard-charge, payer-rate, modifier-definition,
modifier-payer, and charge-modifier grains. A single flattened table would duplicate source
evidence, obscure ordinals, invent a primary code, lose modifier resolution failures, and invite
aggregation of percentages with dollar amounts. The original source path can also change during or
after a build, so a content digest alone is insufficient provenance unless the admitted bytes remain
available. Modifier definitions can also be scoped to inpatient, outpatient, both, or no stated
setting; canonical-code matching without that scope can select an inapplicable definition.

## Decision

1. Keep DuckDB in an optional `lakehouse` extra. The base streaming and inspection layers remain
   dependency-free.
2. Hash the exact source bytes with SHA-256, copy and re-hash them into an immutable build snapshot,
   then promote that snapshot to a content-addressed archive. Inspection and normalization read the
   snapshot, not the mutable original path.
3. Normalize the charge-information and optional modifier-information arrays one element at a time
   into bounded TSV spools, then let DuckDB bulk-load them under declared schemas. PyArrow is not
   required.
4. Materialize and export 13 explicit models across raw, staging, intermediate, finding, and mart
   layers. Partition Parquet by publisher, source period, file version, and run identity.
5. Define a run as the SHA-256 of pipeline version + publisher identifier + source-content identity
   + inspection `as_of` + transformation fingerprint. The fingerprint covers the declared SQL,
   model/export DAG, spool shape, parser and normalization policy, manifest schema, inspection
   policy/finding catalog, and contracts. Identical tuples verify and reuse one run; a transformation
   or inspection-policy change produces disjoint artifacts even when publisher, input, and
   assessment date are unchanged.
6. Retain the exact admitted JSON element text and SHA-256 for every raw item and modifier. Use
   `DECIMAL(38,10)` for typed numeric projections, leaving the raw lexeme authoritative when source
   precision exceeds that scale. Retain all item codes as ordered `codes_json`; do not treat source
   code ordinal zero as a semantic primary code.
7. Preserve modifier definitions, charge references, and payer-plan mappings as separate grains.
   Resolve with a documented NFKC/trim/case-folded key and only definitions applicable to the
   charge-group setting; label a match `exact` only when original text also agrees. Include the
   selected `modifier_setting` and all `candidate_modifier_settings`. Expose known-but-inapplicable
   definitions as `setting_mismatch` / `modifier_setting_mismatch`. Permit same-canonical inpatient
   and outpatient definitions, but reject pairs whose applicable settings overlap (including
   missing or `both` settings).
8. Preserve dollar, percentage, and algorithm representations as separate intermediate rows. Only
   stated dollar observations enter `mart_segmented_dollar_rate`; methodology remains a required
   comparison segment.
9. Enforce types, exact raw hashes, accepted code sets and methodologies, positive numeric values,
   uniqueness, source ordinals, reconciliation, and referential expectations as executable
   contracts. A violation rolls back model rows and promotes no completed snapshot.
10. Write a schema-v4 manifest containing separate transformation and inspection fingerprints plus
    the source archive and all 13 Parquet artifacts with relative path, byte size, and SHA-256.
    Digest every immutable manifest field into `manifest_body_sha256`, excluding only `status` and
    the digest itself. Promote it in `prepared` state, commit the successful catalog row, then
    atomically replace it with `success`. Reuse verifies the body digest, catalog/manifest identity,
    and every artifact before returning. If finalization was interrupted after commit, a validated
    `prepared` manifest is finalized on the next reuse.
11. Configure DuckDB with an operator-visible memory limit, a warehouse-local spill directory, two
    worker threads by default, and insertion-order preservation disabled. Source ordinals carry
    order; physical insertion order does not. Record rows, bytes, wall time, DuckDB peak-buffer
    signals, and requested/effective settings while stating that `memory_limit` is not an RSS cap.
12. Run deterministic local inspection as part of each new run, persist one deduplicated
    `file_finding` row per code with occurrences and citations, and embed the full inspection in the
    manifest. Remote retrievability remains outside this local path.

## Consequences

- The streaming credibility claim remains attributable to standard-library code; DuckDB begins at
  the lakehouse boundary.
- The archived source makes a completed run independently auditable after the original is changed
  or removed, at the cost of one additional content-sized artifact per distinct source digest.
- Raw exact text plus typed projections make coercion visible. `DECIMAL(38,10)` gives deterministic
  warehouse arithmetic but does not claim unlimited source precision.
- The spool temporarily uses disk roughly in proportion to normalized content. The DuckDB file,
  source archive, and Parquet exports intentionally duplicate storage for different operational and
  publication roles; their sizes are recorded rather than hidden.
- Physical indexes are not created on high-volume model tables. Contracts enforce uniqueness
  transactionally, avoiding an index-memory cost that scales poorly.
- Insertion-order preservation is disabled. Stable keys, contracts, counts, archived input, and
  logical results are the cross-build reproducibility boundary; independent Parquet builds are not
  promised byte-identical.
- The manifest protocol closes the tested post-commit-finalization gap and reuse fails closed on
  corrupt inventories or immutable manifest-body tampering, including altered inspection,
  envelope, or metrics. Individual filesystem promotions are no-overwrite atomic operations, not
  a claim that a multi-file snapshot appears atomically under process death.
- Concurrent writers, historical warehouse migrations, and a full SIGKILL/fsync matrix across
  persistence boundaries are not implemented. Current deterministic fault tests cover partial
  promotion cleanup and interrupted post-commit manifest finalization.
- The final clean 64,828,148-byte acceptance completed in 46.66 seconds at 534,790,144 bytes
  maximum process RSS and a 575,865,768-byte macOS peak memory footprint. DuckDB's retained
  512,212,992-byte `system_peak_buffer_memory_bytes` value is a profiler metric, not process RSS or
  a value to compare directly with the configured `256MB` buffer-manager setting. Full evidence is
  in `docs/PHASE-2-FINDINGS.md`.
- All 11 real-file modifier definitions omitted optional setting, so setting restriction behavior
  is established by synthetic regressions for mismatch reporting, disjoint definitions, and
  overlapping-setting rejection rather than by that one-file acceptance.
- Hospital CSV and payer-MRF shapes remain future adapters. This decision does not force them
  through the CMS v3 hospital normalizer.
