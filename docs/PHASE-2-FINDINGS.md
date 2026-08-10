# Phase 2 validation: one real CMS hospital JSON v3 snapshot

Measured 2026-08-09 on the final worktree using macOS 26.4 arm64, Python 3.14.5, and DuckDB 1.5.5.
This is a clean one-file acceptance of `hospital-json-v2`, not a multi-publisher result or scale
forecast. The full quality gate passed: 153 tests at 91.00% branch coverage, with ruff and
`mypy --strict` clean.

## Input and reproduction command

The ignored local source was 64,828,148 bytes with SHA-256
`8feba71a2c937584c0f899ac9cb5ac42df831091dc53882f3c0290bfad6796bd`. It identified University
of Cincinnati Medical Center, CMS hospital JSON template `3.0.0`, and `last_updated_on`
2026-04-01. It contained a UTF-8 BOM.

The acceptance invocation used the following options against a clean warehouse destination:

```sh
/usr/bin/time -l .venv/bin/mrf-honest ingest data/cache/uchealth.json \
  --publisher-id uchealth \
  --publisher-name "University of Cincinnati Medical Center" \
  --warehouse warehouse-final \
  --as-of 2026-08-09 \
  --memory-limit 256MB \
  --threads 2 \
  --format json
```

The successful run identity was:

```text
run_id: e267a22befa20735e0396e017d587d149190416a9cf2b988225e45479120b184
pipeline: hospital-json-v2
manifest schema: 4
transformation fingerprint: 81c716c95c83d26c8e141a9e6c075772bcd58af1b6325d1f305a25310962b7b5
inspection fingerprint: 8814ef06642fa34206b857c570debbdc31f9cb884446eafa94e67b7d11412908
manifest_body_sha256: d5d56cf43ac67ff80f5afaf38d60ce2856c7c787e6dcb6459caaa3d0d6ad95f6
```

Run identity hashes pipeline version, publisher identifier, source SHA-256, inspection `as_of`,
and transformation fingerprint. The fingerprint covers the declared schema and transformations,
manifest schema, export model set, spool shape, normalization/parser policy, inspection
policy/catalog fingerprint, and contracts. A transformation or grading-policy change therefore
cannot silently reuse an artifact built under older semantics.

## Execution and memory

The clean lakehouse run completed in 46.66 seconds real time (46.09 user, 1.72 system). macOS
reported 534,790,144 bytes maximum RSS (510.02 MiB) and a 575,865,768-byte peak memory footprint
(549.19 MiB).

DuckDB was configured with `256MB` and two threads; DuckDB reported an effective memory setting of
`244.1 MiB`. The largest retained `system_peak_buffer_memory_bytes` profiler signal was
512,212,992 bytes. That value is a DuckDB profiler metric: it is not process RSS, not a per-model
memory delta, and must not be compared directly with the configured memory setting. The configured
setting applies to DuckDB's buffer manager, not the complete Python/DuckDB process.

## Rows and source reconciliation

The source completed with zero parser problems and zero raw payload-hash mismatches:

| Grain | Rows |
|---|---:|
| source items / staged charge items | 30,114 |
| item codes | 66,070 |
| standard-charge groups | 35,045 |
| payer rates | 247,423 |
| intermediate rate observations | 247,423 |
| modifier definitions | 11 |
| modifier-payer mappings | 737 |
| charge modifier references | 536 |
| comparison-eligible stated-dollar rows | 192,778 |

The file profile retained every rate representation and its comparison eligibility:

| Methodology | Representation | Eligible for segmented dollar comparison | Observations |
|---|---|---:|---:|
| fee schedule | dollar | yes | 192,778 |
| other | algorithm | no | 48,736 |
| percent of total billed charges | percentage | no | 5,909 |

No percentage or algorithm row entered `mart_segmented_dollar_rate`. Of the eligible dollar rows,
55 carried nonempty resolved modifier context: 40 resolved the modifier and payer-plan mapping by
exact source text, and 15 resolved the modifier exactly and the payer-plan mapping canonically.
No unresolved context was observed among those 55 rows.

All 11 modifier definitions in this real file omitted the optional `setting`, so this run did not
exercise a setting restriction. Synthetic regression tests provide that evidence: resolution is
limited to definitions applicable to the charge setting; `modifier_context_json` retains
`modifier_setting` and `candidate_modifier_settings`; and non-applicable known definitions emit
`setting_mismatch` with payer status `modifier_setting_mismatch`. Disjoint inpatient/outpatient
definitions may share a canonical code, while definitions whose applicable settings overlap fail
the `unambiguous_canonical_code_setting` contract.

The local inspection emitted only three deduplicated findings: the tolerated BOM and separate
interpretability findings for algorithm and percentage representations. Retrievability remained
`NOT_ASSESSED` because the run began from a local file. These findings are observations, not a CMS
compliance determination.

## Storage and integrity

The clean completed warehouse produced:

| Artifact class | Size |
|---|---:|
| DuckDB catalog | 117,977,088 bytes |
| 13 Zstandard-compressed Parquet files | 52,459,578 bytes |
| content-addressed source archive | 64,828,148 bytes |
| source archive + 13 Parquets | 117,287,726 bytes |
| nine transient TSV spools | 251,678,531 bytes |

The 117,287,726-byte immutable payload total is the source archive plus the 13 Parquets; it does
not include the JSON manifest. Spools are measured transient build inputs and are removed after the
run. The DuckDB catalog is retained for local querying but is not counted in the immutable artifact
sum.

The schema-v4 manifest records the source, counts, inspection, transformation and inspection
fingerprints, model DAG, contracts, DuckDB settings, per-model metrics, and path/size/SHA-256 for
the archived source and every Parquet. Its `manifest_body_sha256` covers every immutable manifest
field, excluding only `status` and the digest field itself so `prepared` can finalize to `success`
without changing the body identity. Reuse verifies that digest before trusting the manifest;
tampered inspection, envelope, or metrics fail closed. It then verifies manifest/catalog identity
and re-hashes all 14 payload artifacts.

A warm verified reuse completed in 0.34 seconds real time at 63,668,224 bytes maximum RSS
(60.72 MiB) and a 34,980,368-byte peak memory footprint with `reused: true`; it re-hashed the
source archive and all 13 Parquets. Missing, unsafe, duplicate, size-mismatched, or digest-mismatched
artifacts fail closed. Physical Parquet bytes are not promised to match across independent clean
warehouses because insertion-order preservation is disabled; archived input, stable keys,
contracts, counts, logical results, and a run's verified inventory are the reproducibility boundary.

## What remains unproven

- This run covers one hospital file. The documented multi-publisher query has not been executed
  over multiple real publishers.
- Hospital CSV and payer-MRF adapters do not exist; there is no payer-MRF pipeline.
- The inspector implements selected deterministic CMS-v3 checks; it does not run the official CMS
  validator or make a compliance determination.
- Small-cell suppression and uncertainty intervals are phase-4 work. Nothing here is ready to
  publish as a price comparison.
- Concurrent writers and historical warehouse migrations are unsupported.
- Crash tests cover handled partial promotion and interrupted post-commit manifest finalization,
  not a full SIGKILL/fsync matrix across every persistence boundary.
- Broad scheduled retrieval still needs a robots policy, per-host pacing, and `Retry-After`
  handling.
