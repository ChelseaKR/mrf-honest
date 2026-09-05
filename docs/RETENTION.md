# Retention: the local blob cache

The only place this project holds bytes it did not write is the fetch cache, and
`SECURITY.md` contemplates one of those files turning out to contain
individual-level data. A retention rule is what says how long such a file could
sit on disk before anyone looks. This is that rule.

## What is stored, and where

`--cache-dir` is a **required** argument on `fetch`, `discover` and `scorecard`.
There is no default path, so the cache never lands somewhere the operator did not
name, and there is no hidden copy in a home directory to forget about.

Inside it:

| Path | Holds |
|---|---|
| `blobs/<sha[:2]>/<sha>` | the retrieved file, content-addressed by SHA-256 |
| `metadata/<sha256-of-url>.json` | validators and provenance for one URL |
| `.tmp/` | in-flight writes, promoted atomically |

The URL is hashed rather than used as a filename, so a directory listing does not
disclose which publishers were fetched. That is a deliberate property and should
survive any change here.

**Nothing in the cache is committed.** The repository carries assessments,
manifests and ingest evidence, which are derived and re-derivable; it does not
carry publisher bytes.

## How long

- **Blobs: keep only as long as a re-run needs them.** The cache exists to avoid
  re-fetching a file whose validators have not changed, not to build a corpus.
  Delete a blob once its assessment is committed, and always before archiving or
  sharing a machine.
- **Metadata: keep.** It is small, carries no file content, and is what makes a
  conditional re-fetch cheap and a provenance claim checkable.
- **`.tmp/`: nothing should survive a run.** A file left there is a failed write,
  and is safe to delete unconditionally.

## Destroy on discovery

If a fetched file is found to contain individual-level data:

1. **Do not commit anything derived from it.** Stop before assessment.
2. Record only the SHA-256, source URL, fetch time and byte count. That is enough
   to identify the file to its publisher and carries none of its content.
3. Delete the blob, then the `metadata/` entry for its URL.
4. Report it to the publisher, per `SECURITY.md`. It is a disclosure to report,
   not a dataset to analyze.
5. Open an incident per `docs/incidents/README.md`, `sev1`.

Step 2 before step 3 is the ordering that matters: destroy the file, keep the
fact that it existed.

## What this policy does not cover

Backups and disk snapshots of the operator's own machine. Content-addressed
deletion cannot reach a copy this project never made, and pretending otherwise
would be the kind of claim the rest of this repository refuses. If a cache
directory has ever been backed up, the destroy step above is incomplete and the
incident record should say so.
