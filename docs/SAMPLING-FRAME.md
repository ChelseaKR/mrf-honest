# How cohort subjects are chosen

*Written 2026-08-19, when the published cohort grew past its first six files.*

The first published cohort was six files from four large academic health systems. Nothing was
wrong with any individual grade in it, and every number on the site traced to a run. But the
cohort had no sampling frame: the four systems were reached for because they were known, their
domains were obvious, and their files were large enough to exercise the streaming reader. That
is a **convenience sample**, and a convenience sample supports exactly one kind of statement —
"here is what these six files looked like on this date" — and no statement at all about hospital
price-transparency publishing in general.

The distinction matters more here than it would elsewhere, because this project publishes dated
letter grades beside the names of real institutions. A reader who sees five **A**s and one **C**
will form an impression of the landscape whether or not the page claims to describe one. The
honest fix is not a disclaimer. It is a stated frame, so that what the cohort can and cannot
support is a property of the method rather than of the reader's charity.

## The frame

The 2026-08-19 cohort is drawn from **two strata**, each a complete enumeration, neither
involving any discretion at selection time.

### Stratum B — a seeded random sample of the CMS acute-care universe

**Universe.** The CMS *Hospital General Information* dataset, dataset `xubh-q36u` in the CMS
provider-data catalog, retrieved 2026-08-19 via
`https://data.cms.gov/provider-data/api/1/datastore/query/xubh-q36u/0`. This is CMS's own
enumeration of Medicare-certified hospitals — the same agency whose rule this project grades
files against. The retrieval returned **5,419 facilities**.

**Inclusion rule**, applied mechanically and before any file was fetched:

| Filter | Reason |
|---|---|
| `hospital_type == "Acute Care Hospitals"` | 45 CFR part 180 reaches hospitals as defined there; this is the closest available field. |
| `state` in the 50 states + DC | Excludes territories, where the rule's application is not something this project has checked. |
| `hospital_ownership != "Government - Federal"` | Federally owned facilities (VA, DoD, IHS) are outside 45 CFR § 180.20's definition of a hospital. |

**3,024 facilities** survive the filter. Their CMS certification numbers, sorted ascending, are
committed verbatim at
[`data/frames/2026-08-19.eligible-facility-ids.txt`](../data/frames/2026-08-19.eligible-facility-ids.txt),
because CMS refreshes this dataset and a frame that cannot be reconstructed is not a frame.

**Draw.** 48 facilities, uniformly at random without replacement:

```python
random.Random(20260819).sample(eligible_facility_ids_sorted_ascending, 48)
```

The seed is the collection date. `tests/test_published_claims.py` re-runs exactly this draw
against the committed identifier list and fails if the recorded sample is not what the seed
produces, so the sample cannot drift and cannot be quietly curated after the fact.

**The sample size was extended mid-run, from 24 to 48, and that is worth stating plainly.** The
first 24 were drawn, attempted, and found to yield only six candidate JSON files, which is too
few to say much. Extending was legitimate here for one specific reason: for this seed and this
population, `sample(ids, k)` is a *prefix* of the seeded permutation — verified on CPython 3.12
and 3.14 — so `sample(ids, 48)[:24] == sample(ids, 24)`. Drawing 24 more could not change which
facilities had already been drawn, could not drop one that had already produced an inconvenient
result, and adds facilities that are still uniformly random. Sequential sampling with a stated
stopping point is ordinary; sequential sampling that reshuffles what it already saw is not, and
this is the former. What the extension *was* informed by is the observed JSON yield, so the
decision is recorded here rather than presented as the plan all along.

**Every drawn facility is attempted and every outcome is recorded**, in the cohort manifest at
[`data/cohorts/2026-08-19.json`](../data/cohorts/2026-08-19.json) — as a graded row where a CMS
JSON file was retrieved, and as a recorded exclusion with its basis and reason otherwise. A
cohort quietly pruned of the targets that failed would be a more flattering artifact and a
dishonest one.

### Stratum A — carry-forward

Every subject published in the 2026-08-14 cohort is re-retrieved and re-graded on the new date:
all six files, all four systems, no exceptions. This is also a complete enumeration with no
selection freedom — the rule can only retain subjects, never choose among them. It exists so
that a published grade is never silently withdrawn, and so the two cohorts can be compared over
time.

Stratum A is a convenience sample and remains one. Carrying it forward does not launder it.

## Resolving a facility to an origin

`cms-hpt.txt` lives at the root of *the website the hospital selected to host its file*, and
neither the CMS dataset nor any other public dataset this project found records that website.
Resolving a sampled facility to a domain is therefore the one manual step in the frame, and it
is the frame's weakest joint.

The procedure, and its guard:

1. A candidate domain is proposed for the sampled facility (its own site, or its parent system's).
2. `mrf-honest discover` retrieves `https://<domain>/cms-hpt.txt` through the project's
   politeness gate.
3. **The document must name the sampled facility.** A returned `cms-hpt.txt` is accepted as the
   right origin only when one of its location entries matches the drawn hospital; the MRF URL
   graded is that entry's, not the domain's first entry. This makes a correct resolution
   self-verifying: the hospital's own file says so.
4. Where a candidate domain failed, the domain was re-checked against independent sources before
   the failure was recorded, because *a wrong guess that 404s must never be published as a
   hospital that did not publish*. The origin actually checked is named in every exclusion.

This step is honest but not reproducible the way the draw is, and the cohort should be read with
that in mind. **In this run, ten first-pass candidate origins were wrong and were corrected before
anything was recorded** — a hospital's branded website is frequently not the host it selected, and
vendor and CDN subdomains follow no convention that could be guessed. One of the ten,
`trhealth.org`, is a parked domain belonging to an unrelated health system in another state. Had
those candidates been accepted at face value, ten hospitals would have been published as having
failed to publish; two of them, as it turns out, publish conforming JSON and are graded here. An
eleventh re-check confirmed the original candidate rather than correcting it. The origin actually
checked is recorded for every drawn facility in
[`data/frames/2026-08-19.frame.json`](../data/frames/2026-08-19.frame.json) and named in every
published exclusion.

## What the draw actually produced

Of the 48 facilities drawn, every one was attempted and every one is accounted for:

| Outcome | Count | What it means |
|---|---:|---|
| Graded | 11 | the origin published a `cms-hpt.txt` naming the facility, and its `mrf-url` is a CMS hospital JSON document that was retrieved (9) or whose retrieval failed and is graded **F** with the dated reason (2) |
| Format outside profile | 32 | the published file is CSV, ZIP, or a vendor endpoint answering `text/csv` — recorded, not graded |
| TXT unreachable at the resolved origin | 4 | two HTTP 403 to an identified client; two whose `robots.txt` would not verify over TLS, which RFC 9309 § 2.3.1.4 makes a complete disallow |
| TXT published without an `mrf-url` | 1 | the origin's only location entry declares no `mrf-url` field and an empty `source-page-url` |

The 11 graded facilities span 10 states and four CMS ownership categories, including three
for-profit (`Proprietary`) hospitals and two church-affiliated non-profits — none of which the
first cohort contained.

## What this cohort can and cannot support

**It can** support statements about the drawn sample: how many of 48 randomly drawn US acute-care
hospitals published a `cms-hpt.txt` at the checked origin, what file formats those documents
pointed at, and how the retrievable CMS JSON files graded.

**It cannot** support a national rate. The sample is 48 of 3,024 — wide confidence intervals on
any proportion — and it is a sample of *facilities*, not of *files* or of patients or of dollars.
Nothing here is weighted by bed count, discharge volume, or spend.

**Its two strata must not be pooled.** Stratum A is six files chosen because they were known;
stratum B is a probability sample. Cohort-wide totals — the grade distribution on the site
included — mix them and are descriptions of this cohort, not estimates of anything. Where a
proportion is meant to describe hospitals in general, it is computed over stratum B alone and
says so.

**Format is the dominant limit, not grading.** This project implements CMS hospital **JSON v3**
and nothing else. A hospital publishing a conforming CSV is not a hospital with a problem, and
grading its file against a JSON profile would measure the wrong thing; such targets are recorded
exclusions, exactly as an unreachable one is. **32 of 48 — two thirds of the draw — fall outside
the profile**, which is the single most useful number this cohort produced and is not a grade.
The consequence for everything else on the site is blunt: the letter distribution describes
hospitals that chose JSON, and format choice is not random with respect to size, vendor, or
engineering capacity.

**The format rule was fixed before the results were seen**, because deciding per target after
looking at its grade is how a cohort gets curated. A target is graded only where the publication
is a CMS hospital JSON document, and format is read from the publication, never from a failed
request:

1. where the `mrf-url` has a file extension, that extension decides;
2. where it has none, a *specific* declared media type decides (a vendor endpoint answering
   `text/csv` is a CSV publication);
3. where the declaration is generic (`application/octet-stream`), the retrieved document decides
   — in profile if it parses as a CMS hospital JSON document.

A `.json` publication whose retrieval *failed* stays in the cohort and is graded **F** with the
dated reason. That distinction is the whole point: Northside Hospital Duluth and Rio Grande
Regional Hospital both publish JSON and both files answered an error, so they are graded; four
other targets published CSV through extensionless endpoints and are not.

## 2026-08-28: the ZIP publications are readable, and still excluded

The format rule above says a ZIP archive "is a container, not a CSV file, and grading one
against this profile would measure the wrong thing". That is exactly right about grading the
container and says nothing about the document inside it, which is what a hospital actually
published. `mrf_honest.container` now opens the container under stated bounds and selects the one
gradeable member, refusing rather than choosing where there is not exactly one, because choosing
would be this project deciding which file a publisher meant to publish.

**The seven ZIP publications of the committed draw are still recorded exclusions.** Their bodies
were never retrieved, and retrieval is an operator-invoked act under the politeness gate. Nothing
about the committed cohorts changes on this date; what changed is that a future collection can
grade them instead of recording them.

## Revisit

Before the next cohort, or before any statement about hospitals in general is published from
this data, whichever comes first. The open question the frame does not yet answer is whether the
JSON/CSV split observed in stratum B is stable, and whether a CSV profile would change what this
project can honestly say.

## 2026-08-19, later the same day: the CSV profile answered the revisit question

The open question above — whether a CSV profile would change what this project can honestly
say — was answered the way questions should be: by building the profile and running it. A
second assessment profile now implements CMS's CSV v3.0.0 templates (Tall and Wide), and a
sibling cohort, `hospital-csv-v3-2026-08-19`, grades **all 25 CSV-retrievable targets of this
same draw** under it. Nothing was re-drawn and nothing was added: the CSV cohort's target list
is exactly the sibling exclusions recorded as CSV-retrievable, and a test walks the seam
(`tests/test_published_claims.py`) so that no drawn facility can vanish between the two
documents — every CSV-retrievable exclusion is a declared target, every declared target is a
published row, nothing is graded twice, and the 7 ZIP publications remain recorded exclusions.

The accounting for the one committed draw of 48 is now: 11 graded under the JSON profile,
25 published as rows of the CSV cohort (21 graded; 2 robots disallows and 2 files over the
project's size ceiling, stated), 7 ZIP archives recorded and excluded, 4 origins whose
`cms-hpt.txt` could not be retrieved, and 1 whose location entry did not resolve.

**The two cohorts are published side by side and never pooled.** Each was assessed under its
own profile with its own fingerprinted policies, and a distribution computed across both would
compare findings produced by different rule sets. "Two thirds of the draw falls outside the
profile" stops being the headline; what replaces it is stated per cohort, on the site, with the
same fail-closed exclusion discipline this document promised.
