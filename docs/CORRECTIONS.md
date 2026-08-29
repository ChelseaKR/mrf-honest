# Corrections, disputes, and removal

This project publishes dated grades beside the names of real institutions. Sometimes it will be
wrong. This page says what to do about that, and it is deliberately written so that being wrong
about you costs you as little as possible.

Three principles hold everywhere below.

**You do not have to prove anything.** A removal request is honoured on request. You are not
asked to demonstrate that a grade is mistaken, to identify yourself as authorised, or to argue
your case first. If you want a row gone, say which row; that is the whole procedure. The
alternative, a project that keeps publishing about you until you satisfy its evidentiary
standard, is a project that has made itself the judge of its own errors.

**A correction is not adversarial.** A report that this project got something wrong is the most
useful thing anyone can send it. Four of the write-ups in [docs/findings/](findings/) exist
because something was wrong and got found; the record of what has already gone wrong is at the
bottom of this page, with the commit that fixed each one.

**Nothing is quietly rewritten.** A corrected or removed row leaves a stated trace: what was
published, what changed, and when. A published number that silently becomes a different number
is worse than the original error, because a reader who saw the first one has no way to learn
that it moved.

## What this project publishes about you

Only what a public, legally mandated document said on a stated date, plus what deterministic
checks made of it:

- the URL your `cms-hpt.txt` published for a location, with any credentials stripped;
- the SHA-256 and byte size of the body that URL returned;
- findings from the implemented checks, each citing the rule or schema clause it rests on;
- one letter grade under a named, fingerprinted policy;
- the date all of that was observed.

It does not publish contact details gathered during discovery, anything about care, anything
about prices as amounts, or any comparison between organisations.

## How to raise something

Open an issue on [the repository](https://github.com/ChelseaKR/mrf-honest/issues), or write to
the address in [SECURITY.md](../SECURITY.md) if you would rather not do that in public. Use
whichever of these fits; none of them requires the others.

### 1. "This grade is wrong"

Say which row and what you think it got wrong. Helpful, but not required: the date, the URL as
you published it, and what you believe the file contains.

What happens: the retrieval evidence and the assessment are re-read. If the tool misread the
file, that is a defect, it is fixed, and the fix lands with a regression test and a write-up in
[docs/findings/](findings/). If the tool read it correctly, you get the finding, its citation,
and the passage it rests on, so you can disagree with something specific.

### 2. "You retrieved the wrong document"

This one has happened, more than once, and it is the most damaging class of error here, because
it publishes something about *you* that is really about a URL, a redirect, or a domain. Say
which row and what the correct URL is, if you know it.

What happens: the row is corrected or removed, and the retrieval path is examined for the class
of mistake rather than the instance.

### 3. "Remove this row"

Say which row. That is all.

What happens: it is removed from the published dataset, the site, and the API on the next
publish, without a request for justification. The cohort's own accounting will then state that a
row was withdrawn on request, because a cohort that quietly shrinks would misstate its own
denominator, and the statistics layer would compute a share of a population that had been edited
after the fact. The withdrawal note names no reason and asks for none.

### 4. "Do not retrieve our files again"

Say so, and it stops. A `robots.txt` disallow already stops it in code, before any request, with
no override flag anywhere in the project ([`src/mrf_honest/politeness.py`](../src/mrf_honest/politeness.py)),
so the fastest route needs no involvement from this project at all. A direct request is honoured
the same way.

## What this project will not do

- Ask you to prove a claim before acting on a removal request.
- Require an identity, an affiliation, or an authorisation to accept one.
- Publish correspondence about a dispute.
- Argue about a grade in public before answering privately.

## What has already gone wrong here

Every entry names the commit that fixed it. This section is the standing answer to "how do I
know you would tell me": it is what telling looks like.

### Ten hospitals were nearly published as having failed to publish

Resolving a sampled facility to the website hosting its file is the one manual step in the
sampling frame, and in the 2026-08-19 run **ten first-pass candidate origins were wrong**. One,
`trhealth.org`, is a parked domain belonging to an unrelated health system in another state. Had
those candidates been accepted at face value, ten hospitals would have been published as not
having published; two of them in fact publish conforming JSON and are graded. The guard that
caught it is that a returned `cms-hpt.txt` is accepted only when one of its location entries
names the drawn hospital, so a correct resolution is self-verifying
([docs/SAMPLING-FRAME.md](SAMPLING-FRAME.md), commit `0411e87`).

### A web page served where a file was requested was published as a hospital's unreadable file

HTTP status describes the transfer, not the payload. A 200 means a server chose to answer, not
that it answered with what was asked for, and treating "the fetch returned" as "the document is
here" published something that was never the hospital's file as though it were
([docs/findings/wrong-document-attribution-2026-08-19.md](findings/wrong-document-attribution-2026-08-19.md),
commit `12db7ff`).

### A download that stopped early was published as a hospital's unreadable file

The same class, one step earlier: a transfer that ended before the declared `Content-Length` was
inspected as though it were the whole document
([docs/findings/truncated-transfer-attribution-2026-08-18.md](findings/truncated-transfer-attribution-2026-08-18.md),
commit `435270e`).

### A TLS certificate that would not verify was counted against the publisher

A certificate this project could not verify says something about a chain, not about a hospital's
document. It moved from publisher failure to not graded (commit `a9b5946`).

### The first cohort had no sampling frame, and its grades implied a landscape

Six files from four large academic health systems, reached for because they were known. Nothing
was wrong with any individual grade, and a reader seeing five A grades and one C forms an
impression of the landscape whether or not the page claims to describe one. The fix was a stated
frame rather than a disclaimer (commit `0411e87`).

### The letter distribution described hospitals that chose JSON, while implying it described hospitals

Two thirds of the random draw publish CSV. Until a CSV profile existed, every one of those was a
recorded exclusion, and the published letter distribution silently described the minority that
chose JSON (commit `c0d0886`).

### Two fabricated figures were found in this project's own metrics ledger

The ledger's "fabricated figures in docs: 0" row itself carried one: it described the audited
export as 116 pinned distributions when it had always carried 51, and `perf/baseline.json`
described a nine-page audit as ten. Both are now re-derived by a test rather than dated
([docs/ROADMAP.md](ROADMAP.md), commit `ff8ebe4`).

### The spool reader guessed a CSV dialect instead of declaring one

A sniffed dialect is a guess that usually works, which is the worst kind, because the run it
fails on is the run nobody expects (commit `f1b1e82`).

### A test suite counted the wrong number of tests

A ledger row said 262 tests when the merged stack had 324; the number came from one branch and
was never remeasured (commit `c05ddbb`).

### The first published interval excluded its own point estimate

Introducing uncertainty intervals, a property test found on its first run that at
numerator == denominator the Wilson arithmetic evaluates the upper bound as 0.9999999999999999
against an observed 1.0, so a page would have printed an interval that excluded the value it was
computed from. Caught before publication rather than after
([ADR 0007](adr/0007-suppression-uncertainty-and-refusal.md)).

## The pattern in all of them

Six of the ten are the same shape: **this project's own limitation, published as a statement
about somebody else.** A URL that redirected, a certificate that would not verify, a transfer
that stopped, a format the tool did not implement, a frame that did not exist, a distribution
that described a subset. Each one reads, on the page, as a fact about a hospital.

That is why "you retrieved the wrong document" is listed above as its own route, and why a
removal request is honoured without argument. The class of error this project is most likely to
make is the class where the person best placed to notice it is the one it is being made about.
