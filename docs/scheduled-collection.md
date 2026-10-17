# Scheduled re-collection and regression alerts

How a dated grade gets its next dated grade, and how a change is reported. Two commands do the
work and neither one grades anything itself.

## The watchlist

`data/recollections/watchlist.json` lists publishers by an identifier, a display name and the
domain whose `https://<domain>/cms-hpt.txt` is read. A domain is added only when evidence in this
repository ties it to that publisher; `tests/test_recollect.py` checks that each listed domain is
a discovered origin in a committed cohort manifest. No URL is guessed.

## Running it locally

```sh
uv run mrf-honest recollect \
  --watchlist data/recollections/watchlist.json \
  --out-dir data/recollections \
  --cache-dir data/cache \
  --contact operator@example.org \
  --max-files-per-publisher 5
```

`--contact` is required, as it is for every command that opens a connection. The command prints a
short JSON summary and exits `0` when it did its work, including when publishers failed. Exit `1`
means this machine could not do the work: a cache or output directory that would not write, or a
watchlist or earlier result that could not be used.

### What it writes

For a run on UTC date `D`, in `--out-dir`:

| File | What it is |
| --- | --- |
| `D.discovery.jsonl` | One registry record per `cms-hpt.txt` fetch, failures included. |
| `D.assessments.jsonl` | One assessment per JSON file; `D-csv.assessments.jsonl` for CSV. The same rows `mrf-honest scorecard` writes. |
| `D.json`, `D.comparison.json` | A manifest and the comparison built from the assessments, as for a cohort. `-csv` for CSV. Re-derivable with `mrf-honest compare`. |
| `D.recollection.json` | The result document, schema version 1. Written last and atomically; its presence means the date is complete. |

The comparison needs at least two assessed files of one profile. When it cannot be built, the
result document says so under `comparisons`, with the reason.

### The result document

Per publisher: how discovery went (`discovered` or `no_evidence`, with the HTTP status, the
digest of the `cms-hpt.txt` that was read, and the reason when there was none), and per listed
file one of three outcomes.

| Outcome | Meaning | `grade` |
| --- | --- | --- |
| `assessed` | A verified body was read and graded. | the letter |
| `no_evidence` | The retrieval did not produce a verified file. The `reason` says what happened. | `null` |
| `not_attempted` | This run chose not to fetch it: over the per-publisher cap, a URL whose extension does not name a format, or an entry with no usable `mrf-url`. | `null` |

A failure is never a pass and never a letter in this document. Retrieval failures keep the grade
the cohort policy gives them inside `D.comparison.json`, where that policy is the single source of
truth, and the alert layer below declines to score them.

### Idempotence and resumption

- A second run on the same UTC date with the same watchlist prints the earlier result and makes no
  request.
- A run killed part-way leaves its assessments behind. The next run skips files already assessed
  that day and fetches only the rest.
- A different watchlist on a date that is already complete is refused.

### Politeness

Nothing here relaxes a rule the cohorts were collected under. `robots.txt` is fetched and obeyed
in code with no override, one per-host minimum interval is held across all publishers, `Retry-After`
is honored, the client identifies itself with the contact, and conditional requests are sent
against the verified cache. A file listed by several locations is fetched once. By default no
more than 5 distinct files per publisher are fetched.

## Alerts

```sh
uv run mrf-honest alert data/recollections/2026-11-01.comparison.json \
  data/recollections/2026-12-01.comparison.json --format markdown --fail-on-regression
```

`--format` is `markdown` (an issue comment or job summary), `atom` (a feed) or `json`. The command
reads two comparison documents and opens no socket. It calls the same comparison as
`mrf-honest diff`, then sorts the result:

- **Regressions**: a worse letter, or a new error-severity finding, where a verified file was
  read on both dates and the grading policy did not move.
- **Improvements**: a better letter, on the same terms.
- **No evidence this collection**: the newer collection read no verified file. Listed so that a
  publisher who stopped being reachable is not invisible; not scored.
- **Could not be judged**: the earlier collection read no verified file, the URL changed, a
  governing policy fingerprint moved, or the grade moved to or from "not graded".

Exit status with `--fail-on-regression`: `0` nothing regressed, `1` something did, `2` no file
could be judged on both dates (a gate that passes when it judged nothing cannot fail). Without the
flag the command is a report and exits `0`; two comparisons that cannot be compared at all (for
example, different profiles) exit `2` either way.

The Atom feed id is a URN per profile, entry ids derive from the feed id, the file and the later
collection, and `updated` is the later collection's date, so re-rendering the same inputs gives the
same bytes and a reader does not see an alert twice.

An alert is not a compliance finding and does not say why a file changed.

## The workflow

`.github/workflows/recollect.yml` runs the above monthly. It does nothing on its schedule until the
repository variable `MRF_RECOLLECT_ENABLED` is `true`; `MRF_RECOLLECT_CONTACT` must hold the
identifying contact. With `MRF_RECOLLECT_OPEN_PR` set to `true` it also opens a pull request with
the dated files, which is how a collection becomes the baseline for the next one. Without it the
files are an artifact of the run for 90 days. When a regression is found it opens or updates one
issue and closes it once a later comparison finds none. See
[ADR 0010](adr/0010-scheduled-recollection.md) for why it ships off and for the open question
about what the project commits to keep running.

## Limits

- Format is read from the URL extension (`.json` or `.csv`). Extensionless vendor endpoints are
  listed as not attempted until classified with `mrf-honest probe`.
- A hosted runner has no cache between runs, so a monthly run is a cold pass. See the ADR.
- The first collection has nothing to be compared with, and its subject slugs do not match the
  cohorts' hand-assigned ones.
