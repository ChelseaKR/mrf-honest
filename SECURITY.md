# Security Policy

## Supported versions

mrf-honest is a pre-release local project with no releases and no deployed service. Only the
current `master` branch is supported.

| Version | Supported |
|---|---|
| `master` (latest) | yes |
| anything else | no |

## Reporting a vulnerability

There is no public issue tracker yet (the repository is not published). Email the maintainer
directly: `ckellyreif@gmail.com`, subject line `SECURITY: mrf-honest`. When the repository gets a
public remote, GitHub private vulnerability reporting becomes the preferred channel and this file
will be updated to point at it.

## Response targets

- Acknowledgement: within 72 hours.
- Triage and severity assessment: within 7 days.
- Fix or mitigation: timeline communicated at triage; credential exposure or data-exposure
  findings are prioritized.

## Data-handling note

This project ingests price-transparency files that publishers are legally required to make
public. The files contain prices, not patients. If a fetched file is ever found to contain
individual-level data, that is treated as a disclosure incident to report to the publisher, not
as a dataset to analyze (see `docs/CONTEXT.md`, "Constraints").
