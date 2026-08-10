# Security Policy

## Supported versions

mrf-honest is a public pre-release project with no versioned releases and no deployed service.
Security fixes are made on the current development branch and proposed to `master`; no released
version has a support commitment yet.

| Version | Supported |
|---|---|
| current pre-release source | best effort |
| versioned releases | none exist |

## Reporting a vulnerability

Do not disclose secrets or exploitable details in a public issue. Email the maintainer directly at
`ckellyreif@gmail.com`, subject line `SECURITY: mrf-honest`. Ordinary non-sensitive bugs can use the
[public issue tracker](https://github.com/ChelseaKR/mrf-honest/issues).

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
