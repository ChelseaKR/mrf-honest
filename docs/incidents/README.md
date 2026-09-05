# Incidents

One file per incident, named `YYYY-MM-DD-<slug>.md`, using the template below.
Zero incidents to date. That is a count, not an exemption: the convention is
exercised the first time it is needed, and this directory holding only a README
is the honest state rather than a gap.

## What counts as an incident here

This project has no deployed service, no accounts, no on-call rotation and no
users whose session can break. That narrows the list a great deal, and the
narrowing is the point: an incident convention that imagines outages this project
cannot have would not be read when the one it *can* have occurs.

The incidents this project can actually have:

1. **A fetched file contains individual-level data.** The files are
   price-transparency documents publishers are legally required to post, and they
   carry prices rather than patients. If one turns out to carry patients, it is a
   disclosure to report to the publisher, **not a dataset to analyze**
   (`SECURITY.md`, `docs/CONTEXT.md` "Constraints"). This is the only incident
   class with a subject who can be harmed, and it is the reason this file exists.
2. **A published grade is wrong.** The scorecard makes claims about named
   organizations' filings. A grade published from a misread file, a
   wrongly-attributed document, or a comparison across incomparable scopes is a
   public statement about someone else that has to be corrected in public.
3. **A gate that could not fail.** A check that reported success without
   measuring anything means every grade it cleared is unverified. This is an
   incident even though nothing broke, because the evidence chain did.
4. **Retrieval harmed a publisher.** Fetching outside the declared bounds:
   ignoring `robots.txt`, exceeding the byte cap, or hammering a host.

## Severity

The labels exist on the repository and mean this, not a generic scale:

| Label | Means |
|---|---|
| `sev1` | Individual-level data was fetched, retained, or published; or a wrong grade about a named organization is live |
| `sev2` | A gate that cannot fail, or a published claim the code contradicts |
| `sev3` | Degraded or misleading output with bounded blast radius, nothing published wrongly |
| `sev4` | Cosmetic, or documentation only |

Every incident issue carries `incident` plus exactly one `sevN`.

## Template

```markdown
# YYYY-MM-DD <one line, what happened, in plain words>

**Severity:** sevN
**Discovered:** how, and by whom
**Published wrongly:** what a reader could have seen, and for how long. If nothing
was published, say so; that is the most important line on the page.

## What happened

Facts and times. No blame, no adjectives.

## Why it was possible

The mechanism, not the mistake. A person made an error because a system let them.

## What was done

Including anything withdrawn or corrected in public, with links.

## What now refuses it

The gate, test, or refusal added so this cannot recur silently. "We will be more
careful" is not an entry. If nothing mechanical was added, state that plainly and
say why the risk is accepted.
```

## Retention of incident evidence

An incident involving individual-level data must **not** attach the data to the
postmortem. Record the SHA-256, the source URL, the fetch time and the byte
count, which is enough to identify the file to its publisher, and follow
`docs/RETENTION.md` for destroying the local copy.
