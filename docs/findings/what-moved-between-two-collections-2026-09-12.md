# What twenty-four days moved: a re-collection of both cohorts, 2026-09-12

*Findings from re-collecting the `hospital-json-v3` and `hospital-csv-v3` cohorts on 2026-09-12,
against the same subjects collected 2026-08-19. Each describes published files on two dates.
Nothing here is a ranking of any hospital, a statement about care, or a legal compliance
determination.*

## What was done

The 2026-08-19 cohorts were the newest this project published, and on 2026-09-12 they were
twenty-four days old. Both were re-collected: the same 17 JSON and 25 CSV subjects, the same
retrieval policy fingerprint, the same assessment and grade policies, one serial
operator-invoked run per profile. Nothing was re-drawn — re-drawing would have made the two
collections describe different hospitals and the change between them unreadable.

One thing was deliberately **not** replayed. Every target's URL was resolved again from its own
origin's `cms-hpt.txt`, retrieved the same day, rather than taken from the earlier cohort. A
cohort that re-fetches the URL it recorded last time cannot see a hospital move its file; it can
only see the file at an address the hospital may no longer publish. That distinction produced
three of the four findings below.

The evidence is the committed assessment registries
([JSON](../../data/cohorts/2026-09-12.assessments.jsonl),
[CSV](../../data/cohorts/2026-09-12-csv.assessments.jsonl)) and the two comparison documents
derived from them; `mrf-honest diff` relates each to its 2026-08-19 predecessor.

## 1. Two grades moved out of 42, and neither moved because of anything this project did

- **Rio Grande Regional Hospital: F → A.** On 2026-08-19 the URL its `cms-hpt.txt` published
  answered HTTP 409, *"Public access is not permitted on this storage account."* On 2026-09-12
  its `cms-hpt.txt` publishes a different URL for the same file — the Azure storage account
  behind it rotated its shared access signature — and that URL serves a 631 MB CMS v3.0.0
  document that grades **A**. The published grade was correct on the day it was taken and wrong
  twenty-four days later, which is the entire argument for dating a grade and re-taking it.
- **Minden Medical Center: D → A.** It republished under the current CSV template. Its 81,961
  `CMS_CSV_PAYER_WITHOUT_CHARGE` instances, its 4,785 invalid methodology values and its
  `2.0.0` version declaration are all gone. That single republication is why the CSV cohort's
  headline payer-without-charge figure fell from 118,411 to 36,450: the number describes five
  files now instead of six, and the change is a hospital's, not a measurement's.

Everything else held. 40 of 42 subjects carry the same letter as on 2026-08-19.

## 2. Three files moved to a URL that publishes as the same string

Three JSON subjects — HCA Florida Raulerson, Portsmouth Regional and Rio Grande Regional — sit
behind one Azure storage account, and between the two collections it rotated the shared access
signature in its query string. Every one of those three is published today at a URL whose
`requested_url_sha256` differs from the one graded on 2026-08-19.

This project redacts userinfo, query and fragment from a URL before publishing it
(`scorecard`'s `url_publication` rule) and hashes the exact URL. So the change was invisible in
the published string and visible only in the digest — and `mrf-honest diff`, which had always
read the digest and had always been right, printed the finding as:

> a different URL was graded: `https://…RAULERSON…standardcharges.json` ->
> `https://…RAULERSON…standardcharges.json`

The same string on both sides of an arrow, under a sentence saying they differed. The comparison
was correct; the report gave a reader nothing to reconcile it with, and the diff document carried
only the redacted strings, so a machine consumer could not tell a rotated signature from no
change at all. Both now carry the digests, and the report says where the difference lies. Fixed
in this change (`diff_version` 2).

## 3. One origin stopped declaring a file it still serves

`msh.ms.gov` published Mississippi State Hospital's three `cms-hpt.txt` location entries on
2026-08-19. On 2026-09-12 its `robots.txt` answers HTTP 301; under RFC 9309 § 2.3.1.4 a
`robots.txt` this tool cannot read is treated as a complete disallow, so the origin's
`cms-hpt.txt` was not retrieved at all and the origin no longer declares any file to this
project.

The file itself is unchanged and still served: Whitfield Medical Surgical Hospital's CSV
returned HTTP 304 against the verified cache at the URL the 2026-08-19 document published, and
it grades **B**, exactly as before. So the cohort holds a graded row whose origin no longer
names it. That is stated in the CSV cohort's manifest rather than smoothed over by an unchanged
grade, because "the hospital's discovery document no longer points here" is a different fact
from "the file is fine", and a reader who cannot tell them apart has been told something false
about a named hospital.

## 4. Two of the four things that did not change are worth stating

- **Northside Hospital Duluth still answers HTTP 403** to an identified client at the URL its
  own `cms-hpt.txt` publishes. Twenty-four days later, the same refusal. It stays the JSON
  cohort's one **F**, with the dated reason.
- **Williamson Medical Center's published URL still answers HTTP 404**, and stays the CSV
  cohort's **F**.
- **Cedars-Sinai still declares template `2.0.0`** on an 884 MB file that carries the v3.0.0
  envelope element for element ([the original
  finding](superseded-template-version-2026-08-14.md)). Eight months after the effective date
  now, rather than seven.
- **Frederick Health Hospital still publishes the superseded CSV v2.0.0 template**, and is now
  the only file in the CSV cohort doing so. Its 36,135 payer-without-charge instances are 99.1%
  of the cohort's total.

## What this says about a single-dated cohort

Two of 42 grades moved in twenty-four days, and both moved for reasons entirely outside this
project: a storage account rotated a credential, and a hospital republished its file. Neither is
visible to a reader looking at one dated cohort, and neither would have been visible to a
re-collection that reused the URLs it recorded last time. A grade is a statement about bytes a
server returned on one day, and the interesting part is not the letter — it is the letter's date,
and whether the page carrying it tells you how old that date is. As of this change, every page
that publishes a grade states how old the measurement is on the day the page was built, and says
so in words once it passes thirty days.
