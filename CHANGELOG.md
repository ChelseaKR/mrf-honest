# Changelog

All notable changes to this project are documented here, in the
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) format. The project is pre-release with
no version tags yet; until the first dated release (phase 5 of
[docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md)), entries are grouped by date.

## [Unreleased]

### Added

- **A canonical that names each page, and a social card, on every rendered page.**
  Every page already carried a canonical; none carried any Open Graph or Twitter tag, so a
  shared link to a hospital's grade previewed as a bare URL with no title and no
  description. `_shell` now emits `og:type`, `og:site_name`, `og:title`, `og:description`,
  `og:url` and `twitter:card` from the same `Page` the canonical is built from, so a card
  cannot drift into describing a different page than the one it sits on. There is no
  `og:image`: this project ships no image at all, and `perf/resource-budget.json` sets
  `max_request_count.image` to 0 precisely so that adding one is a failed build rather than
  a change nobody noticed, so the card is `summary`, which promises none.

### Fixed

- **The Lighthouse gate scored the error page against a floor that asks it to be
  indexable.** `seo` embeds the `is-crawlable` audit, which fails on any page
  carrying `robots: noindex`, so making the error page correctly non-indexable
  dropped it to 0.63 against a floor of 1.0. Lowering the floor for all 45 pages
  to accommodate one would be the wrong trade, so `NOT_INDEXABLE_ROUTES` exempts
  `/404.html` from the `seo` floor only, and `is-crawlable` is now asserted
  directly and in both directions instead: this route must be blocked from
  indexing, and every other route must not be. That is strictly stronger than
  what it replaces. Nothing previously checked either, and a category score
  cannot tell the two intents apart, since 0.63 looks the same whether a page is
  correctly non-indexable or accidentally so. A missing or null `is-crawlable`
  audit is a failure rather than an assumed pass, matching how every other
  category is treated here.

- **The error page told crawlers its preferred URL was a dead one.** `404.html` is written
  from a `Page` whose `path` is `"404"`, so the canonical built from that path was
  `https://chelseakr.github.io/mrf-honest/404/`, an address that does not exist and that
  itself returns 404. An error page has no canonical URL to state and should not be indexed
  at all; it now carries `robots: noindex` and states nothing.

- **The index's meta description pooled two cohorts the page itself keeps apart.** It read
  "grades for 42 hospital price-transparency files", and that 42 was the sum of every
  cohort's `targeted` computed for the description alone: it was the only place in the
  project that added them up. The page renders each cohort in its own section precisely
  because their rows were assessed under different profiles and must never be pooled into
  one distribution (`docs/how-we-compare.md`), and the figure appeared nowhere in the
  page's own visible text, which states 17 and 25 separately. A number the page declines
  to state is not a number its search result should state either, and a preview card is
  the surface where nobody rechecks it. The description now states no count, and a test
  fails if a digit reappears in it.

  Observed failing four ways: the Open Graph block removed; the error page's `noindex`
  removed so it canonicalises to `/404/` again; the pooled count restored to the
  description; the canonical pointed at the bare shared origin. The deploy check in
  `pages.yml` was extended over the same ground, so it reads all 45 rendered pages rather
  than only the ones the test fixtures cover, and its `robots.txt` line now records that
  the published `site/robots.txt` is inert as deployed, because a crawler only reads
  `/robots.txt` at the origin root and this project cannot publish one there.

- **The release path, up to the one act no automation here should take (phase 14).**
  `.github/workflows/release.yml` verifies that a tag carries a signature from a key in
  `.github/allowed_signers`, requires the tag and `pyproject.toml` to agree, refuses a
  pre-release version, requires a `CHANGELOG.md` entry for it, re-runs `make verify` at the
  tagged commit with the uv cache disabled, and builds and SHA-256-hashes the distributions.
  It holds no signing key, no passphrase and no registry credential; it creates no tag; it
  publishes nothing. `.github/allowed_signers` is deliberately **not** shipped: a committed
  placeholder would look like a configured trust root while trusting nobody, so the workflow
  stops at that step with the reason stated instead, and a test asserts the placeholder is
  absent. Twenty-three assertions in `tests/test_release_workflow.py` pin all of that, including
  that every action is SHA-pinned, no checkout persists credentials, and the version this
  repository declares today (`0.1.0.dev0`) would be refused, which is the honest state of it.

- **Crash and concurrency durability is measured rather than disclaimed (phase 12).**
  `docs/ROADMAP.md` has said since the lakehouse landed that "concurrent writers, historical
  warehouse migrations, and a full SIGKILL/fsync crash matrix remain open". Two of those three
  now have evidence. `tests/test_durability.py` runs a real ingest in a subprocess and kills it
  with SIGKILL at six named progress markers; after every kill the catalog must report no
  completed snapshot, every run it does report must have its manifest and every named Parquet on
  disk, and the warehouse must be re-runnable to completion. The kill points are markers rather
  than wall-clock offsets because offsets were tried first and were flaky: a kill scheduled at a
  fraction of a run measured a second earlier lands wherever the machine's load puts it. The
  sweep also fails outright if fewer than four of its six samples were genuinely killed, so a run
  of quiet non-interruptions cannot pass as evidence. Two writers are raced against one warehouse
  and one source, and one snapshot is the measured result. One observed state is recorded rather
  than asserted away: killing at the instant DuckDB creates `warehouse.duckdb` leaves a file it
  will not open read-only, because the file exists before its header does; a re-run recovers it.
  The interesting part is what the matrix could **not** show: reordering the catalog commit ahead
  of artifact promotion left every marker green, because that window is too narrow for a kill to
  land in. Three deterministic fault injections close it, at promotion, at the Parquet write and
  at the manifest write, and the reordering fails them. What stays open is named rather than
  implied: historical migrations, fsync behaviour (which needs a filesystem fault injector, not a
  signal), and the one-statement window `_clean_promoted` guards, which no fault this suite can
  inject reaches. That last one has a test whose only job is to say so.


- **ZIP publications are read rather than excluded (phase 11, first half).** Seven publications
  in the committed 2026-08-19 draw are ZIP archives, and every one was a recorded exclusion on
  the stated ground that a container is not a CSV file. That is right about grading the
  container and says nothing about the document inside it. `mrf_honest.container` opens an
  archive under stated bounds and selects the one gradeable member, or refuses with the reason:
  at most 64 members, at most 1 GiB of declared uncompressed bytes, at most a 200-to-1 expansion
  ratio on any member (that is what a zip bomb is, and the ratio is read from the central
  directory so nothing is decompressed to find out), no encrypted member, no member name that
  escapes the archive root, and no nested archive, because a nested container is a second
  unbounded read wearing the first one's clothes. Two gradeable members are refused rather than
  chosen between, since choosing would be this project deciding which file a publisher meant to
  publish. A member is classified by its leading bytes, never by the name it was stored under.
  `mrf-honest inspect` reads a ZIP publication end to end, records the container step in its JSON
  output, and removes the lifted member afterwards; a refused container exits nonzero and emits
  no inspection, so an archive nobody opened can never report zero findings. **The seven
  committed ZIPs stay exclusions**: their bodies were never retrieved, and retrieval is an
  operator-invoked act. The warehouse's CSV profile, the other half of this phase, is not built.

- **A correction and removal flow, and the record of what this project got wrong (phase 10).**
  [docs/CORRECTIONS.md](docs/CORRECTIONS.md) says what is published about a named institution,
  gives four routes for raising a problem, and states the rule the rest of the page is built on:
  **a removal request is honoured on request, with no proof asked for and no case to make.** A
  withdrawn row leaves a stated trace in the cohort's accounting, because a cohort that quietly
  shrinks would misstate its own denominator and the statistics layer would then compute a share
  of a population edited after the fact. Two GitHub issue forms match, and a test asserts the
  removal form has exactly one required field and that neither form requires a reason, a proof,
  or evidence. Every page's footer carries the route, asserted for every rendered page rather
  than for the index alone, because a subject who finds a wrong grade lands on that file's page.
  The page ends with ten things that have already gone wrong here, each naming the commit that
  fixed it, and a test resolves every cited commit against this repository and requires it to be
  an ancestor of the default branch. That check needs history to check against, so CI now
  checks out with `fetch-depth: 0`, and a shallow clone fails the suite loudly rather than
  letting the citation checks skip in the one place they run automatically. Six of the ten are one shape: this project's own limitation
  published as a statement about somebody else.

- **A read-only MCP server that refuses what the site refuses (phase 9).** `mrf-honest mcp`
  speaks JSON-RPC 2.0 over stdio, standard library only, and answers entirely from the `api/`
  documents the render wrote. Five tools: `list_cohorts`, `list_files`, `get_file`,
  `cohort_statistics` and `grading_method`. There is deliberately no tool that retrieves a
  hospital's file. The refusals are the point: `list_files` with a grade filter and no
  `cohort_id` returns a stated refusal, because a letter counted across cohorts pools rows
  produced under different profiles and policies, and an assistant would get that number in one
  sentence without ever seeing the page that explains why it is meaningless. A slug published in
  two cohorts refuses until one is named, an unknown tool refuses rather than answering
  approximately, and a cohort published before the statistics layer says so instead of reporting
  an empty result. `grading_method` reads each rule table from the policy the published grades
  were minted under rather than from a summary that could drift from it. A test asserts the
  module names no network module at all. The server is not registered with any MCP registry,
  which would name a released package; there is no release yet.

- **The grades leave the HTML: `dataset.csv`, a Table Schema, and a static JSON API (phase 8).**
  All three are written by the same render as the pages, from the same comparison documents, so
  there is no second pipeline to drift: a row that is not on the site is not in the dataset. The
  dataset carries one row per published file with 34 columns, and every row carries its cohort,
  profile, publisher type, URL provenance and all three policy fingerprints, because a grade with
  no profile beside it invites exactly the pooling `docs/how-we-compare.md` forbids. A refused
  warehouse load is a stated `lakehouse_status`, never a blank. The Table Schema is generated from
  the same column tuple as the CSV header, so a column cannot appear in the data and be missing
  from its own description, and it carries the site's caveat so a consumer who never opens a page
  still meets the boundary. `api/index.json` lists every cohort with its scope, its summary and
  its statistics outcome, including a refusal as a stated field rather than a missing key;
  `api/cohorts/<id>.json` carries the rows, exclusions and finding matrix.
  `mrf_honest.dataset.missing_exports` runs on the deploy path and fails a publish where the
  exports have been truncated, reordered, or dropped.

- **The first population statistic this project has published (phase 7, comparison version 3).**
  The comparison document now carries a `statistics` block, always present so that a refusal
  cannot read as an older document. For the 2026-08-19 JSON cohort it publishes the disposition
  of the seeded draw with a 95 percent Wilson interval on every share: 11 of 48 published as a
  row (22.9%, 13.4 to 36.4), 32 of 48 outside the profile (66.7%, 52.7 to 78.2), 4 of 48 whose
  `cms-hpt.txt` could not be retrieved (8.3%, 3.3 to 19.4), and 1 of 48 published without an
  `mrf-url` (2.1%, 0.4 to 10.8), each with the finite-population correction applied against the
  3,024-facility frame. The disposition labels are read from the manifest's own exclusion `basis`
  values, so a basis this code has never seen appears rather than vanishing into an unaccounted
  remainder, and a test asserts the shares partition the stratum exactly. The other two committed
  cohorts publish a stated refusal instead: 2026-08-14 has no sampling frame, and the CSV cohort
  accounts for 25 of a draw of 48 with its sibling holding the rest, so rescaling to 25 would
  publish a share of a population nobody drew. The site renders a refusal as a paragraph rather
  than omitting the section, and `mrf_honest.site.missing_shares` runs on the deploy path, so a
  share computed and never rendered fails the publish before it deploys. All three committed
  comparison documents were regenerated; `docs/how-we-compare.md`, the README's "Still open" list
  and the metrics ledger follow.

- **Suppression, uncertainty, and the refusal that guards them (ADR 0007, phase 6).**
  `mrf_honest.statistics` is the only place a proportion is produced, and it returns either a
  `Proportion` carrying its own stratum, denominator and interval or a `Refusal` carrying the
  reason no number was produced. There is no third outcome and no entry point returning a bare
  float, so a point estimate cannot be rendered without the qualifiers that make it readable.
  `docs/SAMPLING-FRAME.md` has said since 2026-08-19 that a proportion is computed over stratum B
  alone and that the two strata must not be pooled; that was prose, and this is the gate. Five
  refusals fire: no sampling frame, a convenience stratum, pooled strata (on the count of strata,
  not their kind), an empty denominator, and a denominator below the stated floor of 20. The
  interval is Wilson score at 95 percent with a finite-population correction applied when the
  frame records a universe larger than the sample, which for the committed draw of 48 from 3,024
  is an effective size of 48.758. A property test found, on its first run, that at
  numerator == denominator the arithmetic put the observed 1.0 outside its own interval
  (upper bound 0.9999999999999999); the interval is now clamped to contain its point estimate and
  the case is pinned by a named regression test. Nothing is published by this change: phase 7 puts
  the results in the comparison document and on the site.

- **A plan for phases 6 through 14 ([docs/EXPANSION-PLAN.md](docs/EXPANSION-PLAN.md)).**
  [docs/IMPLEMENTATION-PLAN.md](docs/IMPLEMENTATION-PLAN.md) stopped at phase 5, with four of
  phase 4's five boxes and six of phase 5's seven still unticked. The expansion plan continues it
  in the same form across a two to three year arc: suppression and uncertainty (6), the published
  statistic (7), the dataset and API (8), a read-only MCP server (9), the correction flow and the
  record of what this project got wrong (10), the warehouse's second profile and ZIP containers
  (11), the durability limits the ROADMAP declines to claim (12), the payer publisher class (13),
  and cadence (14). Phase 14 also names, in one place, the four things no automation should do
  here: sign a release tag, decide a service tier, perform the screen-reader pass, and retrieve
  new evidence from publishers' servers.

- **AI narration outside the graded path (ADR 0006, 2026-08-21).** `mrf-honest narrate` explains
  one already-graded assessment record in English or Spanish. The grade and findings are inputs
  the model cannot change; it is shown only passages from the documents the record's own
  findings cite, every claim must quote them verbatim, and `mrf_honest.ai.corpus` verifies each
  quote against the retained copy before the claim is shown, withholding and counting the
  rest. `corpus/SOURCES.json` retains 45 CFR Part 180 (eCFR XML, point in time 2026-08-20) and
  the CMS JSON and CSV data dictionaries (commit `5333564a710f`) with hashes and retrieval
  dates, and records that the CMS policy FAQ PDF is not retained. The `anthropic` SDK arrives
  as an optional `ai` extra that only this layer imports; the standard-library boundary of
  ADR 0002 holds for everything on the graded path. `python -m mrf_honest.ai.eval` measures
  grounding; two recorded runs on Amazon Bedrock `global.anthropic.claude-sonnet-4-6` are
  committed under `evals/ai/results/`: the 17 records of the 2026-08-19 JSON cohort produced 95
  claims, 91 shown (95.8%), 4 withheld (three altered quotes, one uncited statement); 8 records
  of the 2026-08-19 CSV cohort produced 48 claims, 39 shown (81.3%), 9 withheld, eight of them
  uncited statements about files that could not be graded and one a quote with a dropped word. A verified citation proves the passage exists, not that the sentence reads it
  correctly; no person has reviewed the prompt or the Spanish output. `README.md`,
  `docs/RESPONSIBLE-TECH-AUDITS.md`, and `docs/IMPLEMENTATION-PLAN.md` now say "no model on the
  graded path" rather than "no model component"; `docs/ROADMAP.md`'s audited dependency count
  moves from 51 to 71.

- **The majority format is graded, not excluded: a CSV assessment profile and its first real
  cohort (2026-08-19).** Two thirds of the random draw publish CSV rather than JSON, and until
  now every one was a recorded exclusion — the letter distribution described hospitals that
  chose JSON, not hospitals. `cms-hospital-csv-v3` implements CMS's CSV v3.0.0 data dictionary
  for the Tall and Wide templates: general elements matched by name rather than position, the
  twelve conditional requirements, accepted-value sets, and placeholder detection, streamed
  row by row with bounded memory (a 319 MB, 1.5-million-row file inspects in ~21 s). The
  sibling cohort `hospital-csv-v3-2026-08-19` grades all 25 CSV-retrievable targets of the
  committed draw: 11 A, 2 B, 4 C, 3 D, 1 F, 4 not graded with the reason stated. Its first
  findings are written up in
  `docs/findings/csv-profile-first-cohort-2026-08-19.md`: 118,411 payer names with no charge
  beside them (99.7% of them in two files still declaring the superseded v2.0.0 template), a
  hospital declaring template `3.0.1` which CMS never published, and a hospital whose own
  `cms-hpt.txt` points at a 404. The comparison layer refuses to pool the profiles; the site
  renders one clearly scoped section per cohort with derived cross-references, and a new gate
  walks the seam so no drawn facility can vanish between the two documents.
- **A bounded format probe, `mrf-honest probe`.** The 2026-08-19 run downloaded 669,479,338
  bytes to learn that four extensionless targets were CSV. The probe answers the same question
  with one robots-checked ranged GET of ~4 KB, classifying the leading bytes themselves (ZIP
  magic, a JSON opener, an HTML doctype, the CMS CSV header row) rather than trusting a
  Content-Type header. Never a grading input; never touches the cache.
- **The cohort has a sampling frame, and the first one is now on record as not having had one.**
  The published cohort grew from 6 files to 17, but the size is the less interesting half. The
  2026-08-14 cohort was a convenience sample — four large academic systems, reached for because
  their `cms-hpt.txt` documents were already discoverable — and a convenience sample supports one
  kind of statement ("here is what these six files looked like") and no statement at all about
  hospital price-transparency publishing. Nothing on the site said so, which left the disclaimer
  to the reader's charity. The 2026-08-19 cohort is drawn from two strata, each a complete
  enumeration with no discretion at selection time: **stratum B** is a uniform random sample of
  48 facilities drawn with a committed seed from the 3,024 acute-care, non-federal,
  50-states-and-DC hospitals in CMS's own *Hospital General Information* dataset, and **stratum
  A** carries forward every subject the first cohort published, so a grade is never silently
  withdrawn. The frame, its filters, its seed, its weakest joint, and what it can and cannot
  support are written down in [docs/SAMPLING-FRAME.md](docs/SAMPLING-FRAME.md); the eligible
  facility identifiers are committed verbatim at `data/frames/2026-08-19.eligible-facility-ids.txt`
  because CMS refreshes the dataset and a frame that cannot be reconstructed is not a frame.
  `GRADE_POLICY_FINGERPRINT` and the rule table are untouched: this adds subjects, it does not
  re-score anything.
- **Two new gates, because "random sample" and "nothing was dropped" are exactly the claims this
  project should distrust.** `tests/test_published_claims.py` now re-runs
  `random.Random(seed).sample` over the committed identifier list and fails if the recorded
  sample is not its output, and separately requires every drawn facility to appear either as a
  graded row or as a published exclusion with a stated basis and reason. Without the first,
  "random sample" is a word; without the second, a cohort could be curated after the fact by
  deleting whichever targets embarrassed it. The README's quantitative lead — file count,
  publisher count, BOM count, largest file, warehouse count — is now re-derived from the
  committed comparison by a third test, so growing a cohort without editing the prose fails the
  build rather than shipping a stale number.
- **The methods page publishes how subjects were chosen, or says plainly that they were not.** A
  grade distribution invites a reader to generalise from it whether or not the page invites them
  to, so `mrf-honest site` now renders the cohort's sampling frame and its format rule; a cohort
  with no frame renders that fact rather than an empty heading.

### Changed

- **What the expanded cohort actually found, none of which the first six files could have
  shown.** Of the 48 randomly drawn facilities, 11 published a CMS JSON document this profile
  reads and were graded; **32 — two thirds — publish CSV, ZIP, or a vendor endpoint that answers
  `text/csv`**, and are recorded exclusions rather than grades, because a hospital publishing a
  conforming CSV is not a hospital with a problem and grading it against a JSON profile would
  measure the wrong thing. Four origins could not be reached at all (two HTTP 403 to an
  identified client, two whose `robots.txt` would not verify over TLS, which RFC 9309 § 2.3.1.4
  makes a complete disallow) and one served a `cms-hpt.txt` whose only location entry declares no
  `mrf-url` field. The graded distribution moved from `{A: 5, C: 1}` to `{A: 12, B: 1, C: 2, F:
  2}`: the project's first **B** is a conforming file whose own `last_updated_on` is more than a
  year before the assessment date, the second **C** is a file declaring version `3.0` where CMS
  specifies `3.0.0`, and both **F**s are retrieval failures at URLs the hospitals' own
  `cms-hpt.txt` documents publish — one HTTP 403, one HTTP 409 *"Public access is not permitted
  on this storage account."* The blunt consequence, stated in the README and on the site: the
  letter distribution describes hospitals that chose JSON, not hospitals.
- **`Content-Type` recording earned its keep on its first real run.** Four drawn targets publish
  through vendor endpoints whose URLs carry no file extension, or an `.ashx` handler. Without the
  declaration recorded there is no way to tell those from a hospital that published a broken JSON
  document; with it, `text/csv` classifies them as out of profile instead of handing four
  hospitals a spurious `F` built from eight "envelope field missing" errors.
- The bias review that the 2026-08-14 responsible-tech appendix deferred *"until the cohort grows
  past its current composition"* has been run and is appended to
  [docs/RESPONSIBLE-TECH-AUDITS.md](docs/RESPONSIBLE-TECH-AUDITS.md). Its finding is that the
  material bias in this cohort is not in the grade policy but in the *profile*, and secondarily in
  origin resolution — the one manual step in the frame, which is systematically harder for small
  and vendor-hosted hospitals. Ten first-pass candidate origins in this run were wrong (one of
  them a parked domain belonging to an unrelated system in another state); had they not been
  re-checked before anything was recorded, ten hospitals would have been published as having
  failed to publish, and two of those in fact publish conforming JSON and are graded here.

### Security

- **A cohort id reached the filesystem before anything checked it was a published cohort.**
  `mrf-honest mcp` built the path to a cohort document by interpolating the caller's
  `cohort_id` straight into `site/api/cohorts/{cohort_id}.json`, so a tool argument was a
  filename. Reproduced against the real handler: `cohort_id="../../../secretplace/notacohort"`
  answered `list_files` with an unpublished document whose `comparison_scope` was `null` --
  grades with no scope, served by the one component whose stated purpose is to refuse what the
  site refuses, and exactly the claim `docs/how-we-compare.md` establishes may never be
  published. `list_files` and `cohort_statistics` were both reachable; an absolute path, a
  symlink planted inside the cohorts directory, and a real but unlisted file in that same
  directory all served it too, and `cohort_id="../index"` read the API index as though it were
  a cohort. The id is now checked for membership in the published index *before* it becomes a
  path, so the published set is the guard rather than the string's shape, and an id the index
  does not list is refused by name with the cohorts that do exist. An index that itself named
  an escaping id would raise rather than read the file. `tests/test_mcp.py` exercises fifteen
  hostile spellings -- traversal, dot segments, trailing and backslash separators, single and
  double URL encoding, embedded null bytes, absolute paths, symlink and unlisted files -- as a
  set across every tool that takes a cohort id, with the unpublished document planted wherever
  each spelling would land, because a guard verified one case at a time is how a boundary ends
  up holding at four call sites and open at the fifth.

### Fixed

- **The release workflow would have failed its own gate on the first tag push.** `release.yml`'s
  `build` job checked out at the default depth and then ran `make verify`, which runs
  `tests/test_corrections.py`, which asserts the clone is not shallow because a citation nobody
  can resolve is a claim rather than evidence. `ci.yml` was given `fetch-depth: 0` when that
  check went in and `release.yml` never was. Latent, because `release.yml` only fires on a tag
  push and this project has never been tagged; measured against a real `--depth 1` clone of this
  branch, 17 of the 25 corrections tests go red. One line.

- **The metrics ledger still reported the suite this stack replaced.** `docs/ROADMAP.md` and the
  README's Code Quality row both read `92.44%, 445 tests passing, 2026-08-21` while phases 6
  through 14 took the suite to 644 passing and 4 skipped at 92.79% branch coverage. Publishing a
  measured number and then leaving it behind is item 9 of `docs/CORRECTIONS.md`, which this same
  stack added. Both rows now carry the numbers `make verify` produced at this commit on
  2026-08-28.

- **A web page served where a file was requested was published as a hospital's unreadable
  file.** An HTTP 200 that returns an HTML landing page instead of the document was described by
  exactly the sentence a genuinely malformed JSON file earns — measured on the composition path
  on 2026-08-19, the two grade reasons were byte-identical strings: "the
  standard_charge_information array could not be streamed to completion; content that could not
  be read is treated as failed, not passed". Both events are `F` and both are the publisher's,
  so no grade was wrong; what was wrong is that the published sentence asserted something the
  tool had not observed. It said the array could not be read *from the document*, when in the
  landing-page case there was no document. `Content-Type` — the one thing a server ever says
  about what it is sending — was read nowhere and stored nowhere, so the tool had no way to say
  "this URL served a web page". It is now recorded verbatim on `FetchOutcome` and in the cache
  metadata, on every path that has response headers, including the unstorable-body and HTTP-error
  paths where it matters most; a 304 carries forward the declaration made when the bytes were
  downloaded rather than erasing it. Where a document did not stream *and* the server declared a
  media type meant to be rendered for a person, the reason now reads "the server declared
  Content-Type 'text/html' — a web page, not the requested file — and the
  standard_charge_information array could not be streamed to completion; …"; where some other
  media type was declared it is named without inference, because a server may serve HTML under
  any label. **`Content-Type` is deliberately not a grading input and this change does not make
  it one**: a conforming MRF served as `text/html` graded `A` before and still does, the header
  is consulted only *after* a document has already failed to stream, the grade rule table is
  byte-identical, and `GRADE_POLICY_FINGERPRINT` is unchanged, so no grade in any cohort moves.
  Where no declaration was recorded — as in all six assessments of the 2026-08-14 cohort, which
  predate the recording — the historical sentence is reproduced exactly, because an unrecorded
  header and a server that declared nothing are different facts and neither is evidence that the
  wrong document arrived. Written up, with the general rule it is an instance of ("a fetch that
  succeeded is not evidence that the document arrived") and a note on what did and did not
  transfer to the sibling defect in another repository, in
  [docs/findings/wrong-document-attribution-2026-08-19.md](docs/findings/wrong-document-attribution-2026-08-19.md).
- **A download that stopped early was published as a hospital's unreadable file.** CPython's
  `http.client` does not raise `IncompleteRead` when a length-delimited response ends early:
  `HTTPResponse.read(amt)` returns `b""` and closes the connection, with a source comment saying
  that raising there "might break compatibility". The fetcher read that as end-of-body, so a
  partial file was hashed, installed in the content-addressed cache with the server's `ETag`,
  recorded as `fetched`, and inspected — where the truncated JSON produced a
  `JSON_STREAM_INCOMPLETE` **conformance** error and, through the grade policy, an `F` reading
  "the standard_charge_information array could not be streamed to completion; content that could
  not be read is treated as failed, not passed". Measured on the composition path: a body cut to
  60% of its declared length graded `F` with that sentence, byte-identical to the `F` an HTML
  landing page and a zero-byte body earn. That is a dated, spec-cited, false statement about a
  named hospital's document, written from a download this project did not finish, and
  `cohort.py`'s own rule is that a local limit is never attributed to a publisher. It also
  persisted: the truncated blob carried the server's validators, so the next conditional request
  would 304 and revalidate the truncation instead of re-fetching. The declared `Content-Length`
  is now compared against the bytes that actually arrived. A disagreement in either direction is
  a `network_error` — retried, like the connection reset it is, and never installed in the cache
  — whose stated reason names both counts, e.g. "the response body ended after 41 of the
  883973507 bytes the server declared in Content-Length". `Content-Length` counts wire bytes, so
  gzip is compared before decoding, and a gzip stream that ends before its trailer is now
  reported as the short transfer it is rather than as an invalid encoding, which was a permanent
  unretried `content_error` blaming the publisher's file. Per RFC 9112 § 6.1 a declared length
  beside a `Transfer-Encoding` header means nothing, so it is now ignored on both sides of the
  read: such a response was previously refused as `too_large` before a byte was read whenever the
  meaningless number happened to exceed the size ceiling. Written up, with a dated `HEAD`-only
  measurement of how the six real cohort endpoints frame their responses and therefore how much of
  the cohort the guard covers, in
  [docs/findings/truncated-transfer-attribution-2026-08-18.md](docs/findings/truncated-transfer-attribution-2026-08-18.md).
- **A published file page stated an absence of contract evidence without its reason.**
  Cedars-Sinai's page said the file "was not loaded into the contracted warehouse for this
  cohort, so no contract evidence exists for it" and stopped there. The cause was this
  project's own lakehouse, which implements CMS hospital JSON v3.0.0 only and refused a file
  declaring template `2.0.0` (`unsupported hospital JSON template version: '2.0.0'`).
  `docs/how-we-compare.md` is explicit that a project limit is not a publisher failure and that
  the reason is always stated; on a page carrying a named hospital, an unexplained absence
  reads like an unnamed defect in their file. The reason could not be stated because it never
  survived: a refused ingest raised, printed to stderr, and produced no evidence document, so
  the comparison row recorded `"lakehouse": null` and the renderer had nothing to say. There is
  now a `LakehouseScopeRefusal` carrying the reason and the scopes on both sides, `mrf-honest
  ingest` emits it as an evidence document on stdout (still exiting non-zero: no snapshot was
  produced), `build_comparison` records it as a discriminated `status: "refused"` row, and the
  file page publishes it. `comparison_version` moves 1 -> 2 for the schema change. The grade
  policy fingerprint deliberately does not move: warehouse evidence is not a grading input, the
  rule table is untouched, and every grade in the regenerated cohort is unchanged.
- **Two published figures that no gate could check, one of which was never true.**
  `docs/ROADMAP.md` claimed the dependency audit covered "116 pinned distributions"; the
  exported set has 51, and `uv.lock` is byte-identical to the commit that made the claim, so it
  was wrong on the day it was written rather than stale. `perf/baseline.json` described the
  audited surface as "the index, how-we-grade, seven file pages and 404.html" for a six-file
  cohort, i.e. nine pages described as ten. Both are corrected, and both are now re-derived by
  `tests/test_published_claims.py` instead of being dated by hand.
- **Three responsible-tech declarations that later work had made false**, still published in
  `docs/RESPONSIBLE-TECH-AUDITS.md`: that the project has no deployed surface (the site has been
  public since 2026-08-09, as an appendix in the same file says), that the fetcher does not
  retrieve or enforce `robots.txt` (`politeness.py` does, with no override flag), and that SAST,
  secret scanning and dependency auditing remain open (all three ship). The file is append-only,
  so each stale line is marked in place and a dated 2026-08-16 appendix carries the current
  statement, along with what remains genuinely open. `CITATION.cff` said published comparisons
  were "planned, not implemented" while one was live, and now says what exists.
- The finding write-up
  ([docs/findings/superseded-template-version-2026-08-14.md](docs/findings/superseded-template-version-2026-08-14.md))
  is amended, with the correction dated in the document. Checked element by element against
  CMS's V2.0.0 and V3.0.0 schemas, the Cedars-Sinai file carries the v3.0.0 envelope and none of
  the v2 element names it replaced -- `location_name`, `type_2_npi` and the full
  `attestation`/`attester_name`/`confirm_attestation` object are present, `hospital_location`
  and `affirmation`/`confirm_affirmation` appear nowhere in the 884 MB body, and the attestation
  string is byte-identical to the V3.0.0 schema's constant, which is not the V2.0.0 constant.
  The finding and the **C** are unchanged; what changed is that the document now says this is a
  stale version label on migrated content, not an unadopted rule. The README lead said the
  broader thing and now says the narrower one.

- **A TLS certificate failure no longer publishes an ERROR finding against a hospital.**
  Certificate-verification failures were classified as `network_error`, which the scorecard
  maps to a publisher failure: an ERROR-severity `MRF_DIRECT_DOWNLOAD_FAILED` in
  retrievability, citing 45 CFR 180.50, on a page carrying the hospital's name. Re-probing on
  2026-08-15 the two hosts the 2026-08-14 cohort recorded as `txt_fetch_failed`
  (`www.massgeneral.org`, `www.sutterhealth.org`) found both returning HTTP 200 with
  `ssl_verify=0` to curl on the same machine at the same minute, while Python raised
  `CERTIFICATE_VERIFY_FAILED` against an OpenSSL bundle that lacked the roots. The cause was
  the collection client, and the consequence would have been a published claim about two
  hospitals. There is now a distinct `FetchStatus.TLS_VERIFICATION_FAILED` mapped to **not
  graded**, with a note that says plainly that one attempt cannot tell a broken server chain
  from a missing local root, and it is not retried, because three attempts will not grow a
  root and the host pays for the noise.

### Added

- **`.github/dependabot.yml`: a rail for keeping the pinned set current.**
  `make verify` already runs `pip-audit --strict` over the whole exported
  lockfile with no ignore list, which catches a dependency that is already
  known-vulnerable. It does nothing about the window between a fix landing
  upstream and landing here. Weekly `uv` and `github-actions` updates now cover
  that, with the CodeQL action set grouped into one PR: init, analyze, autobuild
  and upload-sarif must run the same version, and since CodeQL Action 3.30.4 the
  non-init steps hard-error on a configuration file written by a different one.
- **Three standards the README conformance table had left out**: AI Development
  Measurement, Incident Response, and Data Governance. Each is declared with what
  exists and what does not, rather than with a posture the repo has not built.
  The Performance row's N/A for the k6 latency rows also had its reason moved to
  sit directly after the N/A, where it reads as a reason rather than as an
  afterthought; the claim itself is unchanged.

- **A gate on the published artifact itself, not only on the code that generates it.**
  `tests/test_published_claims.py` re-runs `build_comparison` over the committed assessments,
  manifest and ingest evidence and requires the committed `*.comparison.json` back byte for
  byte. Nothing checked this before: the site renders a committed document, so a change to the
  comparison layer, the grade policy or the finding catalog could ship green while the artifact
  on disk -- and therefore every number on the site -- still described the old behaviour. The
  same derivation runs on the deploy path in `.github/workflows/pages.yml`, because `verify` is
  a separate workflow and a red run there does not by itself stop a publish.
- **The ingest evidence documents themselves**, under `data/cohorts/<date>.ingest/`. Until now
  the only copy of each ingest result lived inside the derived comparison, so the derivation had
  no inputs to be re-run against and could not be checked at all.
- **A page-per-row check in the publish workflow.** It previously asserted one number from the
  comparison and the sitemap line in `robots.txt`, both of which a render that emitted no file
  pages would still satisfy. Every row in the comparison must now have its own rendered page
  that the index links to.
- **Retrieval politeness in code** (`src/mrf_honest/politeness.py`), replacing the operator
  procedure that `docs/ROADMAP.md` recorded as a scope limit on any broad retrieval.
  `robots.txt` is fetched before the first request and obeyed, with no flag that skips it: a
  `Disallow` matching the `mrf-honest` product token is a hard stop, an unreachable `robots.txt`
  is a complete disallow (RFC 9309 section 2.3.1.4), and a 4xx means none exists and the fetch
  may proceed (section 2.3.1.3). A per-host minimum interval is held across a whole run and a
  `Crawl-delay` can lengthen it but never shorten it. `Retry-After` on 429 and 503 is honoured
  ahead of this tool's own backoff. Every decision and every wait is retained as JSON-safe
  evidence for the registry.
- **`FetchStatus.ROBOTS_DISALLOWED`**, mapped to **not graded** rather than F. A host that asks
  not to be crawled has not failed to publish, and grading a hospital F for our own compliance
  with its `robots.txt` would be a false statement about that hospital.
- A localhost-server test suite (`tests/test_politeness.py`) that drives the real fetch path
  through a real `http.server`, because "the request was not made" can only be testified to by
  a server that would have noticed.

### Changed

- **`fetch_url` now requires a `politeness` argument.** It is required rather than optional
  precisely so that no call site can retrieve anything without having made the decision; a
  default would be a bypass, and a test asserts that no `ignore_robots`, `skip_robots` or
  `force` parameter exists.

- **An accessibility and performance gate for the page this repository actually serves**
  (`.github/workflows/accessibility.yml`). Lighthouse 12 audits every HTML file the render
  produced -- the page list is enumerated from the build, never typed into the workflow, so a
  cohort that grows grows the audit -- and `perf/score_lighthouse.py` fails the run when the
  page list is empty or short, when a report is missing, when a category score is absent or
  null, or when the resource budget is exceeded. The floor is 1.0 on accessibility,
  best-practices and SEO; `perf/baseline.json` carries the committed measurement and the 10%
  ratchet.
- **Contrast and heading-order assertions in `make verify`**, so the half of the gate that
  needs no browser runs on every push: the palette is a single `PALETTE` mapping with a
  declared table of every text-on-background pair, each asserted at 4.5:1, and a colour added
  without a declared pair fails the suite.

### Fixed

- **Two WCAG 2.2 AA defects that were live on the published site.** SC 1.3.1: the index went
  from `<h1>` straight to the file cards' `<h3>` (axe `heading-order`), scoring 0.98. SC 1.4.3:
  the `FINDINGS` status chip and the `WARNING` severity chip rendered `#a35d00` on `#f6ead8` at
  11.2px bold, measured 4.28:1 against a 4.5:1 requirement, on every file page that recorded a
  warning; those pages scored 0.95. A new `--c-ink` token at 5.53:1 fixes the contrast without
  changing the badge colours. All nine pages now score 1.0 on all four Lighthouse categories.

- **`make verify` gained a format gate, a lockfile-drift gate, and a dependency audit**, taking
  it from three checks to six: `ruff check`, `ruff format --check`, `mypy --strict`, pytest with
  the branch-coverage floor, `uv lock --check`, and `pip-audit --strict` over the exported
  lockfile with no ignore list. Each was verified to fail on a deliberately broken input rather
  than merely to pass today. The dependency audit closes the gap the README's Security row had
  been disclosing as tracked.

### Changed

- **Dev dependencies moved from `[project.optional-dependencies]` to a PEP 735
  `[dependency-groups]` group** (CQ-27). `uv sync` now installs the toolchain by default and the
  quickstart drops `--extra dev`; `lakehouse` stays an extra because it is a real installable
  feature of the distribution rather than a build-time convenience.
- **The lockfile-drift check is `uv lock --check`, and CI installs with `uv sync --locked`
  instead of `uv sync --frozen`.** Measured on a deliberately drifted project under uv 0.12.1:
  `uv lock --check` exits 1, `uv sync --locked` exits 1, and `uv sync --frozen` exits 0.
  `--frozen` installs from the lockfile without consulting `pyproject.toml`, so it cannot see the
  two disagree; CQ-09 names it as the drift check and it is not one. Related: a bare `uv run`
  rewrites a stale lockfile in place, so a drift gate invoked that way repairs what it checks.
- **The metrics ledger in `docs/ROADMAP.md` was brought current.** Its AUTO rows still read
  89.78% and 226 tests from 2026-08-09 while the README read 90.73% and 262 from 2026-08-14; both
  now read the re-measured 90.88% / 324 as of 2026-08-15, and the ledger states its own dating
  convention so mixed dates are legible rather than ambiguous.

- **Hosted security scanning** (`.github/workflows/security.yml`, conventions from the sibling
  fhir-scorecard workflow): CodeQL over both the Python code and the workflow YAML, plus a
  checksum-verified, version-pinned gitleaks binary walking the full git history, on push, pull
  request, weekly schedule, and manual dispatch. The verify workflow's checkout no longer
  persists credentials it never uses.
- **First written-up finding**
  ([docs/findings/superseded-template-version-2026-08-14.md](docs/findings/superseded-template-version-2026-08-14.md)):
  an 884 MB hospital MRF that still declares CMS template 2.0.0 as retrieved on 2026-08-14 —
  more than seven months after CMS's documented v3.0 effective date — with the retrieval
  evidence, the content digest, what cuts the other way (the envelope already carries the
  v3-required fields; the file streams cleanly; its annual update window is intact), and a
  correction path. Also records the cohort-wide RFC 8259 BOM pattern (four of six files) as the
  tolerated INFO observation it is.
- **README rewritten around what the tool found**: the first graded cohort leads (five A, one
  C), followed by what a grade is and is not, with the honest scope statement, the quickstart,
  and the standards table updated for the site's accessibility/i18n/CI scope.
- **Static scorecard site** (`mrf_honest.site`, `mrf-honest site`, `.github/workflows/pages.yml`):
  one indexable page per graded file with the grade and its one-sentence reason, all five
  dimension statuses, every finding with severity, occurrence count, method-page anchor, and
  primary-source links, item/charge/rate counts, warehouse contract evidence (or its stated
  absence), and verification provenance (requested URL, observation time, decoded size, content
  SHA-256, record digest). Index with the honest coverage statement and recorded-but-not-graded
  targets; a methods page stating what is and is not checked; sitemap, robots.txt, 404, and a
  machine-readable `data/comparison.json`. Dependency-free HTML, `lang` attribute, skip link,
  no JavaScript. The Pages workflow rebuilds only from committed data and fails closed if the
  rendered numbers disagree with the comparison document; it deliberately contains no scheduled
  collection.
- **Cross-file comparison layer** (`mrf_honest.cohort`, `mrf-honest compare`): turns one attested
  collection run of persisted assessments into a published comparison with one deterministic
  presentation grade per file under a separate, versioned, fingerprinted policy
  (`file-grade-v1`). The assessment artifact stays rank-free; the grade lives in the comparison
  output only (ADR 0005, `docs/how-we-compare.md`). Fail closed throughout: a failed download is
  a stated `F` with the dated reason, a project limit is `NOT_GRADED` and never conflated with
  `F`, an incomplete stream is an `F`, and a dimension without evidence counts exactly like one
  with errors. Comparison is refused without a manifest attesting one operator-controlled
  collection run — the encoding phase 3 said must exist before any remote comparison is
  published.
- **First real cohort** (`data/cohorts/2026-08-14.*`): six hospital MRF subjects across four
  health systems, discovered via `cms-hpt.txt`, retrieved in one serial identified run under the
  default policy with robots.txt checked per host, assessed, and compared. Every number in the
  committed comparison output is generated by `mrf-honest compare` from the persisted rows.
  Recorded-but-excluded targets (a `.zip` publication, two TXT network errors, one TXT 404) are
  first-class manifest entries with basis and reason, not silent omissions.
- Phase 3 remote-plus-local scorecards: explicit hospital/location/URL provenance, exhaustive
  terminal-fetch semantics, source-cited retrievability findings, honest coverage denominators,
  cross-scope comparison refusal, portable semantic IDs, full-body integrity digests, and an
  atomic single-writer assessment registry. `scorecard` is also available as the `grade` alias.
- CMS `cms-hpt.txt` discovery now models all five required fields and repeated multi-location
  entries. Registry schema v2 persists every entry and retains backward reading for v1 evidence.
- ADR 0004 documents why mutable retrieval evidence is a separate artifact from content-derived
  lakehouse runs and how operator/infrastructure failures avoid publisher attribution.
- Phase 2 `hospital-json-v2` lakehouse: optional DuckDB catalog, bounded TSV normalization spools,
  a content-addressed exact-source archive, 13 partitioned Parquet model exports, and a documented
  raw → staging → intermediate → mart DAG.
- Schema-v4 run manifests with `prepared` → catalog commit → `success` finalization, integrity
  records for the source archive and every Parquet artifact, and `manifest_body_sha256` over every
  immutable manifest field. Reuse now fails closed on tampered inspection, envelope, or metrics.
- Idempotent identity over pipeline version, publisher, content, inspection `as_of`, and a
  transformation fingerprint that incorporates the inspection policy/catalog fingerprint.
- Raw item and modifier models that retain exact admitted JSON element text plus SHA-256; typed
  `DECIMAL(38,10)` numeric projections; ordered all-code `codes_json`; and separate modifier,
  modifier-payer, and charge-modifier grains with explicit exact/canonical/unresolved context.
- Setting-aware modifier resolution with selected and candidate settings, explicit
  `setting_mismatch` / `modifier_setting_mismatch` states, support for disjoint inpatient and
  outpatient definitions sharing a canonical code, and contract rejection when applicable
  settings overlap.
- Executable data contracts for model types, exact raw hashes, source-scoped uniqueness, source
  ordinals, references and reconciliation, CMS v3 code sets/settings/methodologies, positive
  amounts, derived-rate context, representation eligibility, modifier relationships, finding
  classifications, and mart denominators. Violations roll back database rows and promote no
  completed Parquet snapshot.
- Deterministic local file inspection with independent retrievability, conformance, completeness,
  interpretability, and freshness dimensions; a complete source-cited finding catalog; bounded
  evidence samples; and no composite score or compliance label.
- Identified HTTPS fetcher with conditional requests, content-addressed verified cache, decoded
  size limits, gzip handling, backoff, safe-redirect checks, atomic metadata, and structured named
  outcomes.
- Append-only JSONL registry for dated discovery and retrieval attempts, including failures.
- Operator CLI commands: `discover`, `fetch`, `inspect`, `ingest`, `profile`, and `explain`, with
  canonical JSON output and a packaged `mrf-honest` console entry point.
- SHA-pinned GitHub Actions verification/build workflow for Python 3.12 and 3.14.
- ADR 0003, model-DAG documentation, grading-method documentation, and measured phase-2 findings.
- Final real-file acceptance on the 64,828,148-byte UC Medical Center source: 30,114 items,
  247,423 rate observations, 11 modifiers, 737 modifier-payer mappings, 536 charge modifier
  references, zero parser/hash reconciliation problems, 13 verified Parquets, and verified warm
  reuse. The current phase-3 gate is 226 tests at 89.78% branch coverage with ruff and mypy clean.
- Portfolio standards conformance pass: `CHANGELOG.md`, `CONTRIBUTING.md`, `SECURITY.md`,
  `CITATION.cff`, `CODEOWNERS`, ADR log (`docs/adr/`), `docs/I18N.md`, `docs/ROADMAP.md`
  (observability declaration and metrics ledger), `docs/RESPONSIBLE-TECH-AUDITS.md`,
  `.pre-commit-config.yaml`, `.python-version`, `uv.lock`.
- README: quickstart, Standards Conformance table, and an AI-assisted development disclosure.

### Changed

- Streaming parser now enforces JSON delimiters, reports invalid UTF-8 with bounded evidence,
  retains exact problem totals separately from bounded samples, preserves source ordinals, and
  discards large sibling values without pinning them in memory.
- Discovery URL validation now fails safely for malformed authorities, ports, whitespace, and
  embedded credentials instead of allowing URL-library exceptions to escape.
- Discovery schema v2 retains all five CMS TXT fields and ordered multi-location entries. This is
  a pre-release constructor migration: callers that instantiated the old three-field `Discovery`
  directly must construct `DiscoveryEntry` values instead. Registry v1 reads preserve their
  historical three-field `ok` semantics rather than inventing missing contact failures.
- Dollar, percentage, and algorithm rate representations are modeled separately; only stated
  dollars enter the segmented comparison mart and methodology remains a required segment.
- The comparison mart retains every ordered item code instead of assigning semantic meaning to
  code ordinal zero, and surfaces modifier resolution failures instead of silently dropping them.
- Phase-2 evidence now reports the post-audit measurement: 46.66-second clean build,
  534,790,144-byte maximum RSS, 52,459,578 bytes across 13 Parquets, a 64,828,148-byte archived
  source, 251,678,531 transient spool bytes, and 0.34-second verified warm reuse.
- README status corrected: it still said "planning only, no code yet" after phases 0 and 1 had
  landed. It now describes what is actually built.
- Dev toolchain floors raised to `ruff>=0.15` and `mypy>=1.18` (previously `0.6` / `1.11`; the
  installed tools, ruff 0.16.2 and mypy 2.3.0, already satisfied both, so nothing weakened).

### Fixed

- **A quoted spool field appearing after DuckDB's CSV sniffer sample failed the lakehouse
  load.** The spool writer is `csv.writer` with minimal quoting, so the JSON modifier-code list
  is the rare field that gets quoted. The first real file with charge-level modifier codes
  (Stanford Health Care, retrieved 2026-08-14) put its first quoted field tens of thousands of
  rows in; the sniffer had locked in "no quoting" from its sample and the `COPY` failed. Latent
  until now because the acceptance file's modifier lists were all file-level and every test
  fixture was smaller than one sniffer sample. The `COPY` now declares the writer's exact
  dialect (`QUOTE '"'`, `ESCAPE '"'`), and a regression test pins a quoted field one row past
  the 20,480-row sample boundary.

### Removed

- The previous dangling CLI declaration was removed during the standards pass; this release adds
  it back with a real, tested implementation.

## 2026-08-04

### Added

- Phase 1: streaming JSON reader (`src/mrf_honest/stream.py`). Peak RSS on the 65 MB reference
  file drops from 506 MB (naive `json.load`) to 27 MB, measured; UTF-8 BOM handled rather than
  fatal; property-based tests via Hypothesis. The buffer-refill slice bug found on the way is
  written up in `docs/PHASE-0-FINDINGS.md`.
- Phase 0: measured constraint study (`docs/PHASE-0-FINDINGS.md`) and the `cms-hpt.txt` discovery
  module (`src/mrf_honest/discover.py`).
- Planning documents: `docs/CONTEXT.md`, `docs/DATA-LANDSCAPE.md`, `docs/IMPLEMENTATION-PLAN.md`.
- Tooling: `Makefile` with a `verify` gate (ruff, mypy strict, pytest with a branch-coverage
  floor of 85), Apache-2.0 `LICENSE`, `pyproject.toml`.
