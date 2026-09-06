# Data card

A standalone description of what this project takes in, what it publishes, and
what the published figures do and do not support. The README is the spec and
`docs/DATA-LANDSCAPE.md` describes the formats; this card is the summary a
reader needs before quoting a number.

## What the data is

Hospital price-transparency machine-readable files, which publishers are
**legally required to post publicly** under 45 CFR § 180.50, plus the
CMS-conventional `cms-hpt.txt` documents used to discover them.

**It carries prices, not patients.** No record describes an individual. If a
fetched file is ever found to contain individual-level data it is a disclosure to
report to the publisher rather than a dataset to analyze: see `SECURITY.md`,
and `docs/RETENTION.md` for destroying the local copy.

## Where it comes from

Retrieved directly from each publisher's own origin, in one identified run, with
a contact address sent on every request (`--contact` is required). `robots.txt`
is honored: two CSV targets are recorded as not graded because their hosts say
no, which is a published outcome rather than a silent skip.

Every retrieval records **source URL, provenance tag, fetch time, byte count and
content SHA-256**, so a published grade traces back to the exact bytes graded.

## What is committed, and what is not

| Committed | Not committed |
|---|---|
| assessments, manifests, ingest evidence, comparison documents | the retrieved files themselves |

The publisher's bytes stay in an operator-named cache directory and never enter
git. Everything published is re-derivable from what is committed.

## The frame, and its limits

Two cohorts, one per CMS file format, **side by side and never pooled**:

- **JSON:** 17 files, 15 publishers. 12 A, 1 B, 2 C, 2 F, 0 not graded.
- **CSV:** 25 targets. 11 A, 2 B, 4 C, 3 D, 1 F, 4 not graded.

Eleven of the JSON files come from a seeded random draw of 48 facilities from
CMS's enumeration of 3,024 acute-care, non-federal hospitals across 29 states;
the other six are carried forward from the first cohort rather than quietly
dropped. All 48 drawn facilities were attempted, and the ones not graded are
published as recorded exclusions with the reason found, because "a cohort pruned
of its failures would grade better and describe less."

**Still outside both profiles, and recorded as such:** 7 ZIP archives, 4 origins
whose `cms-hpt.txt` could not be retrieved, and 1 whose location entry did not
resolve.

## What the figures support

- A grade describes **one file at one moment**, against cited CMS rules and
  schema documentation. It is not a statement about a hospital's prices, its care,
  or its compliance posture.
- The two cohorts are **not comparable to each other**. Pooling them would
  describe hospitals that chose a format rather than hospitals.
- **No price comparison is published anywhere**, and none will be until a rate
  comparison can carry its own uncertainty (`docs/EXPANSION-PLAN.md`). Any figure
  in this project is about disclosure quality, not about what care costs.
- Absent data is published as absent. A file that reported nothing is never
  rendered as a zero.

## Known limitations

- One retrieval per subject, so a publisher that fixed a file after the run still
  shows the graded state. The fetch path is operator-invoked; there is no
  scheduled refresh yet.
- A 1 GiB ceiling is this project's own, and two files exceed it. That is stated
  rather than blamed on the publisher.
- The multi-publisher grade distribution has never been executed against a wider
  frame than the draw above.

## Contact

`SECURITY.md` for anything sensitive; the public issue tracker otherwise.
Corrections are welcome and the process is in `docs/CORRECTIONS.md`.
