# Build Status

## Goal

Build a configurable research app that discovers recently launched software products, researches public evidence, verifies facts against source text, captures screenshots, and creates a cited comparison report. Requirements and design context are in [`prompt.md`](prompt.md) and [`autonomous_product_research_pipeline_guide.md`](autonomous_product_research_pipeline_guide.md).

## Current status

**Implemented: full API, research pipeline, dashboard, and daily worker.** The user can start a run from the browser, follow stage progress, inspect decisions and costs, and open the resulting HTML or Markdown report. LLM research requires a valid configured provider key; broad web discovery requires Tavily, while the no-key Hacker News mode searches Show HN posts only.

| Area | Implemented | Remaining problems / next work |
|---|---|---|
| API and frontend | FastAPI routes for health, categories, run creation/status, and reports; responsive dashboard for category management, progress, and report viewing | Add authentication integration/roles if deployed beyond a trusted single-user environment |
| Storage and orchestration | SQLModel records for runs, products, pages, facts, screenshots, comparisons, decisions, and stage logs; persisted stage outputs and completed-stage skipping | Existing schema changes have no migration system; SQLite is the supported default in the current deployment |
| LLM layer | Gemini Flash-Lite for bulk discovery/research; Gemini Flash for selection/comparison and screenshot review; Groq GPT-OSS fallback on Gemini HTTP 429; structured-output retry, exact-prompt cross-provider cache, decision logging, and token/cost accounting | Provider quotas remain account-specific; screenshot vision can fall back to deterministic image-quality checks when rate-limited |
| Discovery | Date-bounded Tavily basic search, no-key Show HN, and GitHub public repository signals for current alternatives; requires dated launch evidence for recent-launch comparisons; widens 90/180/365 days and discloses alternatives mode | Product Hunt adapter is not implemented; GitHub repository metadata is current-state evidence, not launch proof |
| Safe collection | URL canonicalization and public-IP checks, redirect screening, bounded HTTP, robots policy, per-host pacing, sitemap recursion/gzip/index handling, same-site filtering, and homepage link/DOM fallback | DNS rebinding and hostile browser content remain residual risks; do not use this as a general-purpose open URL fetcher |
| Research | Sitemap URL groups, constrained page picker, exact-quote fact verification, 24-hour fetch cache, one gap-fill iteration, up to four pages per product, and an 18k-character cap per extraction call; backup-product substitution | Evidence coverage depends on public site content; inaccessible or sparse sites yield partial output |
| Screenshots | Separate persisted Playwright/Chromium stage, URL guard, consent dismissal, lazy-load scroll, blank-image checks, vision review, and accepted-image counts; captures are stored and embedded even for sitemap URLs without text-extraction rows | Three screenshots per product are required by default; inaccessible targets keep the report explicitly partial |
| Site metrics | Persisted seventh stage gathers matched public GitHub repository metadata and Google PageSpeed Insights mobile technical SEO/performance audits; API quota/network errors are surfaced per product and do not fail the comparison | This is technical SEO, not keyword rankings, backlinks, or traffic estimates; those require a dedicated data provider |
| Comparison and reporting | One evidence-linked structured comparison call, deterministic Markdown report generation, and a visual standalone HTML dashboard with comparison matrix, direct-comparison benchmarks, GitHub/PageSpeed signals, screenshots, source cards, and expandable methodology | Email/webhook delivery and retention controls are not implemented |
| Operations | Separate API and APScheduler worker in Docker Compose, shared persistent data volume, configurable schedule and per-run time/cost caps | Postgres migrations, production monitoring/alerts, and deployment-specific resource tuning remain open |
| Tests and checks | Unit/service tests cover URL safety, sitemap parsing, exact-quote verification, rendering, orchestration resume, and injected end-to-end pipeline behavior | Chaos-site/browser integration tests and live provider tests remain open |
| Documentation | Docker/local setup, environment variables, dashboard instructions, API routes, and current limitations documented in [`README.md`](README.md) | Keep setup and limitation notes synchronized with implementation |

## Problems the app is designed to solve

1. Categories and run limits can change without editing pipeline code.
2. Search results are bounded by dates, checked for launch evidence, validated, and widened from 90 to 180 to 365 days only when too few candidates survive.
3. Product pairs retain backups so failures can be replaced where possible.
4. Research follows robots and sitemaps and constrains selected URLs to discovered pages.
5. Public web access has scheme/IP checks, redirect checks, timeouts, size limits, robots handling, and per-host pacing.
6. Extracted facts require exact source quotes; comparison dimensions reference stored fact IDs and unsupported score claims are omitted.
7. Weak or inaccessible sources produce an explicitly partial report instead of fabricated certainty.
8. Stage outputs, decisions, token usage, cost, and errors are persisted; completed stages are reused on resume.
9. Per-run time and estimated model-cost limits prevent unbounded work.
10. A browser UI and a separate daily worker make manual and scheduled operation available.
11. Fewer than two new, validated products mark selection as failed with counts and filter reasons instead of appearing as a successful empty report. Tavily uses basic search to reduce search cost, and wider discovery runs only when the initial window yields too few candidates.

## Verification recorded (8 Oct 2026)

- `.venv/bin/python -m pip check` — no broken requirements.
- Playwright 1.63 and matching Chromium 153 installed; Chromium launch and real page navigation verified. Host OS libraries resolve. The app container image was not built in this verification.
- Live configured Tavily search, Gemini structured generation, and Groq structured generation were all exercised. Gemini returned HTTP 429 during a later stage; the configured Groq fallback handled the structured comparison.
- Real run #37 completed discovery, URL validation, selection, research, screenshot capture, and report rendering. Discovery found insufficient dated launch proof and disclosed a current-alternatives comparison. The validated products were researched; three real Chromium screenshots per product passed capture review and all six are embedded in report #12.
- The first final-report attempt exposed redundant narrative-writer JSON failure after comparison had succeeded. The pipeline now renders the evidence-linked report deterministically from the structured comparison, removing two model calls. Rerun #37 completed with `status=completed`, six completed stages, and `partial=false`.
- `.venv/bin/python -m compileall -q app tests`, `node --check app/static/app.js`, `.venv/bin/pip check`, `.venv/bin/pytest -q` — passed; 38 tests.
- Real GitHub and PageSpeed calls reached the APIs for both products. The current network's anonymous GitHub search quota returned HTTP 403, and PageSpeed returned HTTP 429 daily-quota exceeded. These are exposed in the report; the seventh stage and final report completed without treating optional metrics as product-research failures.
- The final seven-stage run report #13 retained all six screenshots and displayed per-provider quota messages. Authenticated GitHub access and an enabled/keyed PageSpeed API project are needed to retrieve these metrics reliably in future runs.
- Existing third-party API keys posted in chat should be rotated; do not commit `.env` or paste secrets into logs.

## Acceptance checklist

- [x] Change category through the UI/API without code changes.
- [x] Queue and inspect manual runs.
- [x] Persist stage status, LLM decisions, errors, and cost information.
- [x] Ground facts in fetched text and verify quoted evidence.
- [x] Produce HTML and Markdown reports, including explicit partial results.
- [x] Include screenshot capture and minimum-count enforcement with failure reporting.
- [x] Provide separate API and scheduled worker deployment configuration.
- [ ] Run chaos-site and real-browser integration suites.
- [x] Verify against live configured providers and real target sites through screenshot generation and final HTML rendering.
- [x] Verify live GitHub/PageSpeed API error handling and final report behavior under quota exhaustion.
- [ ] Add schema migrations, monitoring/alerts, and deployment retention controls.

## Run locally

See [`README.md`](README.md), especially **Quick start**, **Using the dashboard**, and **Current limitations**. Minimum setup is to copy `.env.example` to `.env`, add Anthropic, Tavily, and admin keys, install the package and Chromium, then run `uvicorn app.main:app --reload` and open `http://localhost:8000`.
