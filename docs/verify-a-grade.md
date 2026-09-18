# Verify a grade

*For anyone who does not want to take this project's word for it: a hospital checking a grade
published about its own file, a reporter checking one before quoting it, a regulator checking
one on the record.*

The README says every published grade can be re-derived from its source. This is the procedure.
It needs no account, no network at verification time, and no trust in this project — only the
file, the receipt, and this tool.

## What a receipt is

Every published row has one, at `api/receipt/<publisher>/<location>.json`. It records:

| Field | What it is |
| --- | --- |
| `requested_url` | the URL the bytes were retrieved from |
| `content_sha256` | the SHA-256 of the exact decoded bytes that were graded |
| `size_bytes` | how many bytes those were |
| `as_of` | the date freshness was measured against |
| `profile` | which CMS v3 profile the file was graded under |
| `grade_policy_version`, `grade_policy_fingerprint` | the exact presentation-grade policy |
| `tool_version` | the version of `mrf-honest` that produced the grade |
| `grade`, `grade_reason`, `findings[]` | the result, and every finding behind it |
| `re_derivable`, `not_re_derivable_reason` | **whether this grade can be reproduced at all** |

## Doing it

```sh
# 1. Get the receipt for the row you care about.
curl -O https://chelseakr.github.io/mrf-honest/api/receipt/<publisher>/<location>.json

# 2. Get the file, from the hospital's own URL, which the receipt names.
curl -o standardcharges.json "$(python -c 'import json,sys;print(json.load(open(sys.argv[1]))["requested_url"])' <location>.json)"

# 3. Re-derive.
mrf-honest verify <location>.json standardcharges.json
```

`verify` opens no socket. It re-hashes the file, re-runs the same fingerprinted inspector under
the policy version the receipt names, and reports whether the grade and every finding reproduce.

## Reading the answer

| Exit | Means |
| --- | --- |
| `0` | The grade and every finding reproduced. |
| `1` | The bytes are the bytes the receipt describes, and something differs. Differences listed. |
| `2` | **The check could not be performed.** |

Exit `2` is not a failed verification. It means no verification happened, and it never says
anything about the file's quality. There are four ways to get it:

- **The hashes do not match.** These are not the bytes the receipt describes. Almost always this
  means the hospital has republished the file since it was graded — which is usually good news,
  and is exactly why the grade is dated. Both hashes are printed.
- **The receipt names a grade policy this build does not have.** The two grades are not
  comparable. Reporting that as a difference would blame the hospital's file for a change in
  this repository.
- **The receipt declares a `receipt_version` this build cannot read.**
- **The receipt was issued as not re-derivable.** See below.

## The rows that cannot be re-derived, and why they still have receipts

Of the 48 published rows, **seven have no verified body**. Four were never retrieved at all;
three stopped part-way through the download. In every one of those cases there are no graded
bytes in existence, so there is nothing anybody — including this project — can re-derive.

Those rows still get receipts. A row with no receipt would read as an oversight, and someone
would eventually ask why that one was missing. Instead the receipt says:

```json
{
  "re_derivable": false,
  "not_re_derivable_reason": "the file was never retrieved, so no bytes were ever graded",
  "grade": "NOT_GRADED"
}
```

and `verify` refuses it by name rather than running a check that could only ever fail.

This matters more than it looks. The alternative — issuing a receipt that looks like every other
receipt, letting `verify` run it against whatever file the reader has, and reporting the
inevitable hash mismatch — would produce a confident-looking result implying that a named
hospital's file had changed, out of the fact that this project never managed to download it.

## What reproducing a grade does and does not establish

It establishes that this policy, applied to these bytes, gives this result. That is all, and it
is the whole point: the grade is checkable rather than authoritative.

It is not a certificate of validity, it is not the official CMS validator's verdict, and it is
not a finding of compliance by anyone. Every receipt carries that sentence in its own `notice`
field, and so does the badge's title.

## The badge

Each row also publishes `badge/<publisher>/<location>.svg` — a small image stating the grade,
carrying a `<title>` and `role="img"` so a screen reader announces the whole claim: the grade,
the hospital, the date, the policy version, and that it certifies nothing. A hospital is welcome
to embed it. It is an SVG document that no page on this site embeds, so publishing badges leaves
the site's zero-image request budget exactly where it was.

## If a re-derivation disagrees

That is a finding about one of two things, and which one matters. If the bytes match and the
result differs, either this tool has changed in a way that was not recorded as a policy change —
which would be a defect here, and belongs in
[`docs/CORRECTIONS.md`](CORRECTIONS.md) — or the receipt is not the one for that file. Please
[open a correction](https://github.com/ChelseaKR/mrf-honest/issues/new/choose) with both hashes
and the receipt; a published grade nobody can reproduce is precisely the thing this project
exists not to publish.

## Related

- [Before you post it](before-you-post-it.md) — the same inspector, run by the publisher.
- [How we grade](how-we-grade.md) — every finding code and its citation.
- [How we compare](how-we-compare.md) — the comparison boundary and the grade policy.
