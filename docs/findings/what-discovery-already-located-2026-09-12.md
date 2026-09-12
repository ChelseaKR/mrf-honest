# What discovery had already located and nobody had counted, 2026-09-12

*Derived from the local discovery registry and the committed assessment registries by
`mrf-honest census`. No request was made to produce it: every `cms-hpt.txt` it reads was already
retrieved, and no MRF byte was moved. Nothing here is a grade, a ranking, or a compliance
determination about any hospital.*

## The two numbers

| | Count |
|---|---:|
| Files **graded** in a committed cohort | **41** distinct URLs (42 rows per collection date, 45 distinct URLs across all three) |
| Files **located** — this project holds the origin, the URL, and a candidate format | **550** |

**That is 13× the inventory, and it cost nothing.** It is not 13× the grades — a located file has
not been read, and no letter is claimed for it. What it has is an *address*, which is the half
that was expensive.

## Why the located half is the expensive half

`docs/SAMPLING-FRAME.md` says it plainly: *"`cms-hpt.txt` lives at the root of the website the
hospital selected to host its file, and neither the CMS dataset nor any other public dataset this
project found records that website. Resolving a sampled facility to a domain is therefore the one
manual step in the frame, and it is the frame's weakest joint."* That step was wrong on **ten of
the first forty-eight** candidate origins.

But a `cms-hpt.txt` names *every location the origin covers*, so one successful retrieval — a few
kilobytes — resolves that joint for all of them at once. Across the origins already retrieved, 49
documents name **656 locations**. Grading is the other cost, and it is the one that scales per
file: a mean of 182 MB, and up to 884 MB.

So the shape a request-shaped product needs is not "grade everything". It is: **locate broadly,
because that is cheap per origin and was the manual bottleneck; grade on demand, because that is
expensive per file and only the files someone asked about need it.** Twenty-five named hospitals
cost about 4.5 GB and a few minutes of streaming; three thousand cost about 350 GB and roughly a
day of single-threaded inspection.

## The full count

```
census as of 2026-09-12
49 origin(s) with a readable cms-hpt.txt; 15 produced no body (a fact about a request, never
  about a publisher); 3 answered with something no location could be read out of
656 location(s) listed, 2 of them with no mrf-url
550 distinct file(s) located
  214 cms-hospital-csv-v3-candidate
  192 cms-hospital-json-v3-candidate
   27 format_not_determinable_from_the_url
  117 outside_the_implemented_profiles
41 of those graded in a committed cohort; 509 located and not graded here
  (this project's collection scope, not a defect)
~ 4 graded row(s) match no located file
```

**406 of the 550 are a candidate for a profile this project implements** (192 JSON + 214 CSV) —
files it could grade today without resolving a single new origin.

## Five things the count refuses to do

Each is enforced in `src/mrf_honest/census.py` and pinned by a test, not left to a careful reader.

1. **A URL extension is a candidate, never a determination.** `.json` in a path is what the
   publisher named the file, not what the server serves. The 2026-08-19 run downloaded
   **669,479,338 bytes** from four hospitals to learn that four extensionless targets were CSV,
   which is why `mrf-honest probe` exists. Every label above says `candidate`.

2. **"We cannot tell from here" is its own population.** The 27
   `format_not_determinable_from_the_url` files — vendor handlers, `.ashx` endpoints, extensionless
   API paths — are *not* counted among the 117 outside the implemented profiles. "We do not know
   what this serves" and "this is a format we do not grade" are different statements about a named
   hospital.

3. **An origin that could not be read is not an origin that publishes nothing.** Fifteen produced
   no body: **nine** `robots.txt` documents this tool could not read (RFC 9309 § 2.3.1.4 makes
   that a complete disallow — and **six of the nine are TLS trust failures from this client**,
   which `scorecard.py` already refuses to attribute to a publisher anywhere else), and **six**
   HTTP errors (three 404, two 403, one redirect loop). Each is carried with its own status and
   reason, and none enters any count about publication.

4. **An HTTP 200 that is a web page is kept apart from a refused request.** Three origins answered
   with a body out of which no location could be read, every one of them carrying the parser's own
   *"served HTML rather than a cms-hpt.txt document"*. Counting those with the refusals would say
   a server refused a request it answered — and an HTTP 200 error page read as content is this
   portfolio's dominant defect class seen from the outside.

5. **No share is computed against the 3,024-hospital frame.** The frame enumerates CMS
   *facilities*; this enumerates *locations named by documents*, and the join between them does
   not exist. Publishing "550 of 3,024" would require silently treating every unresolved facility
   as something. The frame's own numbers are printed beside the census, labelled, and the report
   says in terms that the count *"is NOT a denominator for the counts above"*. A test asserts that
   every population is an integer, so a float — a share across a join nobody has — cannot appear.

And one more, which is about people rather than counts: **contact details are never carried.** A
`cms-hpt.txt` entry names a person and their email. `docs/CORRECTIONS.md` promises this project
does not publish "contact details gathered during discovery", so the census reads four fields by
name and never copies an entry wholesale. A test serialises the whole document and asserts the
fixture's name and address appear nowhere in it.

## The four graded rows no located file matches

This is expected and is stated rather than smoothed over. Three are HCA Florida Raulerson,
Portsmouth Regional and Rio Grande Regional as graded on **2026-08-19**: one Azure storage account
rotated the shared access signature in its query string, so the URL those rows name is no longer
the URL their origin publishes. The fourth is Whitfield Medical Surgical Hospital, whose origin
`msh.ms.gov` could not be retrieved at all on 2026-09-12.

A grade is a statement about bytes at a URL on a date; a discovery document is a statement about
what an origin publishes today. A hospital moving its file produces this mismatch without anything
being wrong with either record.

## A census that read nothing says so

Found while writing this document, on the one path nothing had exercised: a checkout with **no**
discovery registry. Before it was fixed, the report said

```
0 distinct file(s) located
~ 17 graded row(s) match no located file
```

having looked at nothing at all. Zero located files beside a count of graded rows matching none
of them reads as a finding about every one of those rows, and "no retrieved `cms-hpt.txt`
declares this URL" is not a statement anybody with no document in hand is in a position to make.
`systems.py` carries the same discriminator for the same reason, and it is the defect this
project exists to refuse, committed by this project.

The census now carries a `status` of `counted` or `no_discovery_evidence`, and in the second case
that one count is **`null`, not `0`** — while every other count, which is a count of something
that really was examined, stays zero, because zero is the truth for those. The origins that were
attempted and failed are still printed: the refusal replaces the count, not the report.

## What this does not do

- **It publishes nothing.** The discovery registry is deliberately gitignored
  (`data/registry*.jsonl`), alongside the blob cache, because it contains the contact details
  above. So this is a **command over local operator evidence**, exactly as `mrf-honest systems`
  is, and the row-level inventory is not committed. Committing it is a publication and retention
  decision — what is published beside roughly six hundred named hospitals, and under what
  correction policy — and `docs/CORRECTIONS.md` and `docs/RETENTION.md` are where that decision
  would be recorded. Issue #91 holds the broader version of the same question.
- **It does not resolve a single new origin.** Every count here comes from documents already
  retrieved. Turning 550 into a number that covers the 3,024-hospital frame is #91, and it has a
  manual tail no code removes.
- **It grades nothing.** 509 files have an address and no letter, and the census says so in those
  words.

## Reproducing it

```sh
uv run mrf-honest census \
  --discovery data/registry.jsonl \
  --as-of 2026-09-12 \
  --frame data/frames/2026-08-19.frame.json \
  $(for a in data/cohorts/*.assessments.jsonl; do printf ' --assessments %s' "$a"; done)
```

The registry is local; a checkout without one gets an empty census rather than a wrong one.
