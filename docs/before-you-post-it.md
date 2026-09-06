# Before you post it

*For the people who put a hospital's machine-readable file on the internet: revenue-cycle staff,
compliance staff, and the vendors who build the file for them. Not written for engineers.*

Every defect this project has published about a hospital's file was already in the file on the
day it was posted. A template version that said `3.0` where CMS specifies `3.0.0`. A file that
listed 118,411 payer names with no charge beside any of them. A file whose stated publication
date was seven months old. Nobody had to fetch anything from the internet to see any of it.

This page is about seeing it first.

## What this is

`mrf-honest` reads one file on your disk and tells you what a careful reader would find in it,
with each observation cited to the CMS data dictionary or to 45 CFR 180. It runs in two places
you already have:

- **as a GitHub Action**, so a pull request that changes the file gets the findings as review
  comments on the file itself, before anyone merges;
- **as a pre-commit hook**, so you see them before the commit exists.

Both read a local file. Neither one opens a network connection, sends your file anywhere, or
knows what hospital it belongs to unless you tell it.

## What it is not

**It is not the official CMS validator, and clearing it is not a certificate of anything.**
Nobody has approved your file because a check went green. CMS publishes its own validator; this
is a second, independent reading, and where the two disagree the answer is in the data
dictionary, not in either tool. The job summary says all of this in the job summary, every time,
because a green check is exactly the artefact somebody will eventually screenshot.

It also does not check whether your file is *reachable* — whether the URL resolves, whether the
server sends the right content type, whether your `cms-hpt.txt` points at the right place. Those
are facts about a web server, and this has only ever seen a file on a disk. The published
scorecard on the website grades five dimensions; this grades the four that a local file can
answer, and the letter it prints says which policy it used.

## Using it in GitHub Actions

```yaml
name: price transparency file
on: [pull_request]

permissions:
  contents: read

jobs:
  check:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - uses: ChelseaKR/mrf-honest@<a tag or commit sha>
        with:
          path: standard-charges.json
          # Fail the build on anything CMS's dictionary makes a requirement.
          fail-on: error
          # Optional: also fail if the file would not reach a B.
          min-grade: B
          # Optional: pin the date freshness is measured against, so a rerun of an
          # old commit gives the same answer it gave then.
          as-of: "2026-08-19"
```

Pin the action to a tag or a commit SHA, not to a branch. The grade comes from a policy whose
fingerprint is a property of the commit; floating the version floats the policy with it, and two
runs a month apart could then disagree for reasons that have nothing to do with your file.

Each finding arrives as an annotation on the file, with its citation. The job summary lists them
in the inspector's order, states the grade and the exact policy fingerprint that produced it, and
names any dimension that could not be assessed.

## Using it as a pre-commit hook

```yaml
repos:
  - repo: https://github.com/ChelseaKR/mrf-honest
    rev: <a tag or commit sha>
    hooks:
      - id: mrf-honest
        args: [--fail-on, error, --min-grade, B]
```

## Reading the result

The command exits with one of three codes, and the difference between the last two matters.

| Exit | Means |
| --- | --- |
| `0` | The file was read and nothing you asked to be stopped by was found. |
| `1` | The file was read and graded, and it did not meet a threshold you set. |
| `2` | **Nothing could be graded.** |

Exit `2` is not a bad grade. It is this tool saying it could not read the document: the file
stopped part-way through, or it is a ZIP archive rather than a document, or its leading bytes are
neither JSON nor CSV. An `F` is a statement about a file that *was* read. Those are different
facts and the tool will not merge them, because "we could not read it" is a claim about the
reading and "it is bad" is a claim about your file.

There is one more thing to read carefully. A dimension can come back **not assessed** — most
often *interpretability*, when there are no payer rates to interpret. A dimension that was not
assessed produces no finding, so no `--fail-on` setting can catch it, at any severity. It does
lower the grade, so `--min-grade` can. The summary lists those dimensions by name for exactly
this reason: an absence should never be readable as a pass.

## What the grades mean

| Grade | What it says |
| --- | --- |
| **A** | Every assessed dimension completed with no error and no warning. |
| **B** | No structural errors; some warnings were recorded. |
| **C** | Errors or missing evidence in one of the four local dimensions. |
| **D** | In two of them. |
| **F** | In three or more. |

A grade describes one file under one stated policy on one date. It does not rank hospitals, it is
not a compliance finding, and it says nothing about your prices — only about whether your file
can be read and used by the public it is published for.

## If you think a finding is wrong

It might be. This project keeps a public record of the things it got wrong
([`docs/CORRECTIONS.md`](CORRECTIONS.md)) and a
[correction form](https://github.com/ChelseaKR/mrf-honest/issues/new/choose). A finding with a
citation you can check is the point; if the citation does not say what the finding claims, that
is a defect in this tool and it will be recorded as one.

## Related

- [How we grade](how-we-grade.md) — every finding code, its severity, and its citation.
- [What a grade is, and is not](../README.md#what-a-grade-is-and-is-not).
- The published scorecard: <https://chelseakr.github.io/mrf-honest/>
