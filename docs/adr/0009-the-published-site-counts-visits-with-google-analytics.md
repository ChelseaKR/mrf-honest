# 0009. The published site counts visits with Google Analytics 4

## Status

Accepted - 2026-09-17.

## Context

On 2026-09-17 the owner decided that every public site in the portfolio gets Google Analytics 4,
with its privacy pages and claims updated to match. https://chelseakr.github.io/mrf-honest/ is
one of them.

Until now the pages carried no script. `perf/resource-budget.json` said the site "genuinely has
none", and the README and the metrics ledger repeated it. The site grades hospitals'
price-transparency files. Its readers include people looking up what a hospital publishes about
prices, so a record of which hospital pages were opened is not a nothing. The privacy page says
so plainly, rather than describing the analytics in general terms.

## Decision

`src/mrf_honest/analytics.py` holds the measurement ID, `G-57DWCFVLWQ` (GA4 property 554843057,
with 14-month retention and Google signals off), and renders one inline `<script id="analytics">`.
`mrf-honest site` passes that ID by default, so `pages.yml` publishes it. `--ga4-id ""` renders
a site with no analytics. `render_site()` defaults to none, and without an ID the render is
byte-for-byte what it was: no script, no privacy page, no analytics sentence. That was checked
against `origin/master` over the committed cohorts.

With an ID, every page carries the loader in its head. Every footer carries a sentence saying the
site counts visits with Google Analytics 4, a link to the new `privacy/` page, and an opt-out
control. The loader loads nothing at all (no `dataLayer`, no request, no cookie) unless all four
of these hold:

1. the ID is well formed;
2. the page is served from `https://chelseakr.github.io/mrf-honest/`. A local render, the
   Lighthouse job on 127.0.0.1 and a fork's Pages site all fail this;
3. the browser sends neither Global Privacy Control nor Do Not Track;
4. the reader has not opted out. The footer's "Opt out of analytics" button sets
   `mrf-honest:analytics-opt-out` in localStorage. The key names this project because every
   `chelseakr.github.io` project site shares one origin, and so one localStorage.

When it does load:

- Consent Mode v2 defaults deny the three ad signals everywhere.
- `analytics_storage` is denied in the EEA, the UK and Switzerland, where GA sends cookieless
  pings, and granted elsewhere.
- Google signals and ad personalisation are off.
- `page_location` is the origin and path only.

The site sets no Content-Security-Policy, so none changes.

## The budget

`perf/resource-budget.json` keeps every non-document line at zero. The pages as rendered request
nothing but themselves: the loader is inline, so it is not a request. The Lighthouse job measures
on 127.0.0.1, where the loader returns before fetching anything. On the published address it adds
Google's gtag.js and GA's requests. Those are outside the budget by the owner's decision, and the
budget file, the README and the metrics ledger now say so instead of saying the site has no
script.

The document and total caps are the one number that moves: from 61,440 bytes (60 KiB) to 65,536
(64 KiB). The loader and its footer opt-out add 2,647 bytes to every page, and the index, the
heaviest page at 58,629 bytes, had less headroom than that once response headers are counted.
The first CI run of this change measured it at 62,520 bytes, over the old cap. The loader is
written compactly (2,195 bytes, down from 3,228 when it was laid out for reading), so the widening
pays for the decision and for nothing else. Content growth past 64 KiB is still a failed build.

## Consequences

- The site has a data flow about its readers. Google receives:
  - the page path, which names the hospital file being read;
  - the referrer, browser, device, language and a coarse location it derives from the IP address;
  - scroll, outbound-click and download events;
  - a random client ID in `_ga`/`_ga_57DWCFVLWQ` cookies.

  It is processed by Google in the US. The graded data, the published JSON and CSV exports, the
  CLI, the GitHub Action, the MCP server and the pre-commit hook carry no analytics.
  `docs/RESPONSIBLE-TECH-AUDITS.md` records this in a dated appendix, because that file is
  append-only.
- `tests/test_analytics.py` runs the loader under Node. No ID, the wrong host or path, GPC, each
  form of DNT and the opt-out each load nothing; otherwise GA loads with the configuration above.
  Its negative controls remove a guard, assert that the removal landed, and assert that GA then
  loads.
- Owner follow-up in the GA4 property, with no API: user-provided data collection off, data
  sharing off, and the Data Processing Terms accepted.
