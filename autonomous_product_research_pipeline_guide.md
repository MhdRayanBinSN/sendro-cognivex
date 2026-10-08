# Autonomous Product Research & Comparison Pipeline
## Full Build Guide: Architecture, Plan, and Edge Cases

---

## 0. What this is

An **agentic data pipeline** that runs daily for a configurable software category (e.g. "cold email generation"):

1. Discovers new products in the category
2. Selects two to compare
3. Researches their websites and sitemaps
4. Decides which pages matter
5. Takes 3+ screenshots per product
6. Compares them
7. Publishes a Markdown/HTML report

**Core idea:** the LLM makes decisions (which products, which pages, how to compare). Your code provides the "hands" (search, fetch, screenshot).

### Tools glossary

| Job | Tool | Plain meaning |
|---|---|---|
| Find products on the web | Tavily / Brave Search API | Google for programs: send a query, get links |
| Download a page | httpx | Opens a URL without a browser |
| Read a page's text | trafilatura | Strips menus/ads, leaves readable text |
| Take screenshots | Playwright | Drives a real headless Chrome |
| Make decisions | Claude API | Send text + question, get an answer |
| Remember past runs | SQLite | Simple DB so products aren't repeated |
| Serve results | FastAPI | Small web server: trigger runs, view reports |
| Schedule | APScheduler / cron | Runs the pipeline daily |
| Render | Jinja2 | Turns Markdown + images into an HTML report |

### Sample day

1. **Discover:** Claude writes search queries, Tavily returns links, Claude extracts candidate products.
2. **Select:** Code removes already-covered products, Claude picks the best head-to-head pair.
3. **Map site:** Code reads `robots.txt` and the sitemap to get every URL.
4. **Pick pages:** Claude chooses ~6 useful pages per site and flags which to screenshot.
5. **Extract:** Code fetches and cleans pages, Claude extracts facts with evidence.
6. **Screenshot:** Playwright captures the flagged pages.
7. **Compare:** Claude compares the two fact sets.
8. **Publish:** Code renders HTML, saves it, records both products so they are never picked again.

---

## 1. Design principles

1. **Code is plumbing, the LLM decides.** Every "what/which/how" is an LLM call with structured output. Every "fetch/parse/save" is deterministic code.
2. **Every stage is a function with typed input and output.** Testable alone and resumable.
3. **Persist after every stage.** If it crashes at screenshots, resume there.
4. **Validate LLM output, never trust it.** Schema-check it; verify URLs and claims against fetched data.
5. **Degrade gracefully.** A failed page doesn't kill the run. A failed product triggers a substitute.

---

## 2. System architecture

```
                      ┌─────────────────────────────┐
                      │  FastAPI (API + admin)       │
                      │  POST /runs  GET /reports    │
                      └──────────────┬──────────────┘
                                     │ enqueue
        ┌────────────────────────────▼────────────────────────────┐
        │ Orchestrator (state machine, one Run = many Stages)      │
        │ persists state in DB after each stage, retries, resumes  │
        └───┬──────────┬───────────┬───────────┬──────────┬───────┘
            │          │           │           │          │
        Discover    Select     Research     Compare    Render
                                   │
                              ┌────┴─────────────────┐
                              │ SiteMapper           │
                              │ PagePicker (LLM)     │
                              │ Fetcher (http→browser│
                              │   fallback)          │
                              │ Extractor (LLM)      │
                              │ Screenshotter        │
                              └──────────────────────┘
   ┌───────────────────────────────────────────────────┐
   │ Shared services: LLM client, Search client, Cache, │
   │ Rate limiter, Robots checker, Blob store (images)  │
   └────────────────────────┬──────────────────────────┘
                            │
                  ┌─────────▼─────────┐
                  │ DB (SQLite → PG)   │  Products, Runs, Stages,
                  │ + files on disk/S3 │  Pages, Facts, Reports, LLMLogs
                  └────────────────────┘
```

The **scheduler** (APScheduler or cron) calls `POST /runs` once a day. Run it as a separate process from the API so long runs don't block requests.

### Project layout

```
app/
  main.py            # FastAPI routes
  config.py          # category, limits, API keys (env)
  llm.py             # ask(prompt, schema) -> validated dict
  db.py              # models
  orchestrator.py    # resumable stage runner
  pipeline/
    discover.py
    validate.py
    select.py
    sitemap.py
    page_picker.py
    fetcher.py
    extract.py
    gapfill.py
    screenshots.py
    compare.py
    verify.py
    render.py
  templates/report.html.j2
  scheduler.py
data/screenshots/
tests/fixtures/
```

---

## 3. Data model

| Table | Key fields | Purpose |
|---|---|---|
| `Category` | id, name, description, active | Configurable categories, no code change needed |
| `Product` | id, name, canonical_domain (unique), url, description, first_seen, status, launch_signal, source | Dedupe key is the **domain**, not the name |
| `Run` | id, category_id, status, started_at, finished_at, error | One daily execution |
| `StageLog` | run_id, stage, status, input_hash, output_json, duration, attempts | Resumability + audit |
| `Page` | product_id, url, fetch_method, status_code, text_hash, cleaned_text, chosen_reason, screenshot_flag | Cache of what was fetched |
| `Fact` | product_id, field, value, source_url, quote, confidence | Evidence-backed facts |
| `Screenshot` | page_id, path, width, height, caption, quality_score | Images |
| `Comparison` | run_id, product_a, product_b, markdown, html, confidence_notes | Final output |
| `LLMCall` | run_id, stage, prompt_hash, model, tokens, cost, response | Debugging and cost tracking |

Run status: `pending → running → partial → completed | failed`.

---

## 4. Orchestration

Use a simple **resumable stage runner** rather than a heavy framework.

```python
STAGES = ["discover", "select", "research_a", "research_b",
          "screenshots", "compare", "render"]

async def run_pipeline(run_id):
    for stage in STAGES:
        if stage_done(run_id, stage):
            continue                       # resume support
        try:
            output = await STAGE_FUNCS[stage](run_id)
            save_stage(run_id, stage, "ok", output)
        except RecoverableError as e:
            handle_retry_or_fallback(run_id, stage, e)
        except Exception as e:
            save_stage(run_id, stage, "failed", error=str(e))
            raise
```

`research_a` and `research_b` can run concurrently with `asyncio.gather`. Later you can swap in Prefect, Temporal or Celery without changing stage code.

---

## 5. Stage contracts

| Stage | Input | Output |
|---|---|---|
| Discover | category | `list[Candidate]` (name, url, description, launch_signal, source) |
| Validate | candidates | candidates passing liveness and quality checks |
| Select | validated candidates, seen domains | `[ProductA, ProductB]` + reasoning |
| SiteMap | product URL | `list[UrlInfo]` (url, lastmod, depth, source) |
| PagePick | URL list, product, category | `list[PagePlan]` (url, reason, screenshot, priority) |
| Fetch | PagePlan | `PageContent` (text, status, method) |
| Extract | PageContent | `list[Fact]` with source and quote |
| Gap-fill | facts so far | extra URLs to fetch if key fields are missing |
| Screenshot | PagePlan where screenshot=true | `Screenshot` files |
| Compare | both fact sets | structured comparison JSON + Markdown |
| Render | comparison, screenshots | HTML + MD saved and served |

---

## 6. Stage-by-stage design and edge cases

### 6.1 Discovery

**Design:** Combine multiple sources, since any single one is biased.
- LLM-generated search queries (regenerated daily, with prior queries passed in so they vary)
- Search API with a recency filter
- Launch platforms (Product Hunt API, Hacker News via Algolia, optionally Reddit or directories)
- "Alternatives to X" pages

**Edge cases:**
- Search returns **blog listicles** instead of products. The extractor must distinguish "a product's own site" from "an article about products".
- Same product under multiple URLs (`app.x.com`, `x.com`, `x.com/?ref=ph`). Canonicalize: strip tracking params, `www`, trailing slashes; key by registered domain.
- **Large incumbents** when you want *new* ones. Ask for a `launch_signal` and `confidence_new` score; reject low scores.
- Zero results. Widen the time window (week → month → quarter), relax queries, then fall back to the best "recent-ish" product.
- LLM invents products. Never accept a candidate whose URL wasn't in the search results (substring check).
- Category too vague or broad. Add a one-time "category expansion" call producing sub-niches and synonyms.
- Non-English results. Detect language; skip or translate.

### 6.2 Validation (liveness and quality gate)

Cheap checks before selecting:
- DNS resolves, HTTP 200 after redirects, not a parked domain
- Meaningful text (e.g. over 300 words)
- Not "coming soon" or waitlist-only (unless you accept those)
- Not a marketplace listing, GitHub repo or social profile pretending to be a product site

**Edge cases:** Cloudflare challenge (403, "Just a moment"), JS-only SPA returning an empty shell (retry with Playwright), redirect chains to an unrelated domain (acquired), expired SSL certs.

### 6.3 Selection

**Design:** The LLM scores each candidate on newness, relevance, comparability and data availability, then picks the pair. Enforce rules **in code**, not in the prompt:
- Exclude domains already in `Product`
- Pair must not be the same company or parent
- Optional cooldown: avoid the same sub-niche two days running

**Edge cases:**
- Fewer than 2 valid candidates: run discovery round 2 with expanded queries, then allow a labeled "new product vs. established benchmark" fallback.
- Products aren't truly comparable (a Gmail plugin vs. a full platform): require a `comparability` score and re-pick below a threshold.
- A product fails research later (site blocks you): keep a **ranked backup list** and swap in the next candidate.

### 6.4 Sitemap and URL discovery

**Algorithm, in order:**
1. Fetch `/robots.txt`, read `Sitemap:` lines
2. Try `/sitemap.xml`, `/sitemap_index.xml`, `/sitemap.xml.gz`, `/wp-sitemap.xml`
3. Recurse into `<sitemapindex>` entries (cap depth and count)
4. If still empty, crawl the homepage: nav, header, footer links, same domain, depth 1 (maybe 2)
5. If still empty or JS-rendered, render the homepage with Playwright and extract links from the DOM

**Edge cases:**
- **Gzipped sitemaps** and wrong `Content-Type` headers
- **Huge sitemaps** (50k+ URLs): stream-parse, group by path prefix (`/blog/*`, `/docs/*`), keep counts per group plus 2-3 samples, prioritize shallow paths
- **Subdomain products** (`docs.x.com`, `app.x.com`): allow same registered domain, flag subdomains
- **Localized duplicates** (`/en/pricing`, `/fr/pricing`): dedupe by path ignoring locale, prefer `en`
- Sitemap lists URLs on **another domain** (staging, CDN): filter to the allowed domain
- **Stale** sitemap full of 404s: check `lastmod`, HEAD-check only chosen pages
- **Hash-routed SPAs** (`/#/pricing`): use DOM link extraction
- Pricing only in a **modal or behind "Contact sales"**: record "not publicly listed" as a finding
- `robots.txt` disallows crawling: respect it for fetching; document a policy for screenshots of public marketing pages and rate-limit heavily

### 6.5 Page picking (LLM)

**Design:** Give the LLM compact info per URL: path, depth, any title or anchor text, and group counts. Ask for a fixed number of pages, each with a **purpose label** (pricing, core feature, integrations, about, docs, changelog, customers, security), a `screenshot` boolean and a rationale.

**Guardrails in code:**
- Output URLs must exist in the input list (else discard)
- Always force-include the homepage
- Enforce min and max counts; if too few, ask again with "choose more"
- **Coverage check:** ask the LLM "which needed topics have no page?" rather than hardcoding paths

**Edge cases:**
- Opaque URLs (`/p/48213`): fetch `<title>` with a cheap partial GET before deciding
- No pricing page: let the LLM say so; the gap-filler looks on the homepage or FAQ
- Everything is on one long landing page: plan **section screenshots** (scroll to anchors)

### 6.6 Fetching

**Fallback ladder:** httpx (realistic User-Agent) → retry with backoff → Playwright render → give up on this page.

**Edge cases:**
- 403, 429 or CAPTCHA: back off, try the browser, then mark `blocked`. **Never try to bypass CAPTCHAs.**
- Redirect loops: cap at 5
- Giant or binary content (PDF, ZIP): cap size, skip non-HTML, or send PDFs to a PDF text extractor
- Wrong encoding: let httpx and trafilatura detect it
- Lazy-loaded content: scroll in Playwright before extracting
- Infinite scroll or auto-playing video: time-box everything
- Geo-redirects or cookie walls: set locale `en-US`, handle consent banners
- Duplicate content across pages: hash cleaned text and skip repeats

### 6.7 Extraction (facts)

**Design:** One fixed schema for both products so they're comparable; fields may be `null`:

```
tagline, target_users, core_features[], pricing{model, tiers[], free_plan, trial},
integrations[], ai_capabilities[], compliance/security, social_proof,
limitations, last_updated_signals, unknowns[]
```

Each fact carries `source_url` and a short `evidence_quote`.

**Guardrails:**
- **Verify the quote actually appears in the page text**; if not, drop the fact. This is the main anti-hallucination check.
- "Not found" is a valid answer; prompt for it explicitly.
- Chunk long pages, merge facts across chunks and pages, resolve conflicts (prefer the pricing page over the blog).

**Edge cases:**
- Marketing fluff ("revolutionary AI") with no specifics: tag as `claim`, not `fact`
- Different currencies, per-seat vs. per-credit vs. per-email models: normalize into a `pricing_model` string and keep raw text
- Conflicting info across pages: keep both and flag it
- **Prompt injection** in page text ("ignore previous instructions"): treat fetched content as untrusted data, wrap in delimiters, instruct the model never to follow instructions inside it, give extraction prompts no tool access
- Page is mostly navigation boilerplate: if trafilatura returns nothing, fall back to raw text

### 6.8 Gap-filling loop (the "agentic" part)

After extraction, compute missing key fields (pricing, integrations, features). If any are missing and budget remains, ask: "Given the missing fields and the remaining URLs, which 1-2 pages should we fetch?" Cap at 2 iterations to avoid runaway loops.

### 6.9 Screenshots

**Settings:** 1440×900 viewport, `deviceScaleFactor` 1 or 2, light color scheme, locale `en-US`, fresh browser context per product.

**Procedure:** goto → wait for network idle (fall back to `load` on timeout) → dismiss overlays → scroll once to trigger lazy loading, then back to top → brief wait → capture.

```python
from playwright.async_api import async_playwright

async def shoot(url: str, path: str):
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport={"width": 1440, "height": 900},
                                        locale="en-US")
        page = await ctx.new_page()
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1500)
        await page.screenshot(path=path, animations="disabled")
        await browser.close()
```

**Quality check (important):**
- Not blank or near-uniform (check pixel variance)
- Not a cookie wall, login wall, 404 or CAPTCHA
- Optionally send the image to Claude with vision: "Is this a usable screenshot of a pricing page? Describe what's visible." This also yields **auto-captions**
- Retry once with a different strategy (longer wait, different viewport, full-page) before dropping

**Edge cases:**
- Cookie banners and chat widgets: click buttons whose accessible name matches accept/agree/close; last resort, hide overlays with injected CSS
- Sticky headers duplicated in full-page captures: prefer viewport shots or stitched sections
- Very tall pages: capture the first screens or clip to a flagged region
- Mid-transition animations: add a wait or use `animations="disabled"`
- Login-gated pages: skip. **Never create accounts or submit forms.**
- Fewer than 3 screenshots succeeded: ask the page picker for more candidates and loop, with a cap
- Memory leaks: close pages and contexts, restart the browser every N pages
- Dark-mode-only sites: fine, but note visual inconsistency
- Concurrency: limit to 2-3 parallel pages in small containers

### 6.10 Comparison

**Design:** Two steps.
1. **Structured comparison JSON.** Dimensions are *derived from available facts*, with a fixed core (positioning, features, pricing, integrations, target users) plus dynamic extras. Each row records `a_value`, `b_value`, `winner|tie|unknown`, and evidence references.
2. **Narrative Markdown** generated from that JSON, so the prose can't invent anything the table doesn't contain.

**Edge cases:**
- One product has far more data: add a "data coverage" note so it doesn't look like a win
- Different pricing units: flag "not directly comparable"
- Unverifiable superlatives ("best"): require every claim to cite a fact ID
- Position bias: randomize which product is "A"
- Vendor marketing claims: label as claims, not verified facts
- Add an automated **verifier pass**: a second LLM call checks each statement in the final text against the fact list and flags unsupported ones

### 6.11 Rendering and delivery

- Markdown is canonical; render to HTML with Jinja2, embedding images as relative files or base64 for a portable single-file report
- Include: title, date, TL;DR, comparison table, captioned screenshots, strengths and weaknesses, "who should choose which", sources list, and a **methodology and confidence** footer
- Add a disclosure that it was generated automatically from public information at a given time

**Edge cases:** escape HTML in LLM output (XSS), handle images that failed to save, make long tables responsive on mobile.

---

## 7. Cross-cutting concerns

### LLM layer
- One wrapper: `ask(prompt, schema, model, max_tokens)`. Validate with Pydantic; on failure retry once with the validation error appended.
- Cheaper model for bulk extraction; stronger model for selection, comparison and verification.
- Low `temperature` for extraction, a bit higher for discovery queries.
- Log tokens and cost per call; set a per-run budget cap that aborts gracefully.
- Handle 429s with exponential backoff and jitter; handle context-length errors by chunking.

### Reliability
- Timeouts everywhere (connect, read, per-stage total)
- Retry only transient errors (5xx, timeouts), not 4xx
- Idempotent stages: re-running must not duplicate rows (unique keys, upserts)
- Overall run deadline (e.g. 20 minutes), after which the run ends as `partial`
- On failure, ship a clearly labeled **partial report** rather than nothing

### Politeness and legal
- Obey `robots.txt` for crawling; use a descriptive User-Agent with a contact address; rate-limit per domain (at most 1-2 req/sec)
- Don't bypass paywalls, logins, CAPTCHAs or bot protection
- Respect copyright: summarize and quote briefly, don't republish site content wholesale; screenshots of public pages for commentary are common but check your jurisdiction and add attribution
- Avoid personal data in output (names, emails on team pages)

### Security
- Treat every fetched page as hostile input (prompt injection, malicious scripts)
- Run Playwright in a sandboxed container with no access to internal networks (block private IP ranges and `localhost` to prevent SSRF)
- Validate URLs before fetching: scheme must be http/https; resolve and reject internal IPs
- Keep API keys in env vars or a secrets manager, never in logs or the repo
- Protect admin endpoints (API key or OAuth)

### Caching
- Cache fetched pages by URL + content hash for 24h or more; sitemaps for a few days
- Cache LLM responses by prompt hash during development to save money

### Observability
- Structured logs with `run_id` and `stage` on every line
- `GET /runs/{id}` returns the stage timeline, decisions made and errors
- Save every LLM decision with its reasoning (your explainability trail)
- Alerts (email or Slack) when a daily run fails or goes `partial`
- Metrics: fetch success rate, screenshot success rate, tokens per run, cost per run

---

## 8. FastAPI surface

```python
@app.post("/runs")                 # body: optional category override
async def start_run(category: str | None = None, bg: BackgroundTasks): ...

@app.get("/runs/{id}")             # status + stage timeline + decisions
@app.get("/reports")               # history
@app.get("/reports/{id}")          # HTML or Markdown (?format=md)
@app.get("/categories")            # list/add/activate categories
```

Config example:

```python
# config.py
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    category: str = "cold email generation"   # change via env/API
    products_per_day: int = 2
    max_pages_per_product: int = 6
    min_screenshots: int = 3
    max_run_cost_usd: float = 5.0
    max_run_minutes: int = 20
    anthropic_api_key: str
    search_api_key: str

settings = Settings()
```

---

## 9. Testing strategy

| Level | What to test |
|---|---|
| Unit | URL canonicalization, sitemap parsing (index, gz, malformed XML), quote verification, schema validation |
| Stage | Each stage on recorded fixtures (save real HTML and sitemaps to `tests/fixtures`) |
| LLM | Golden-file tests with a mocked LLM; a small eval set (~10 sites) checking the page picker returns pricing and feature pages |
| Integration | Full run against a local fake website you control (no sitemap, JS-only, cookie banner, 403) |
| Regression | Re-run on 5 real sites weekly to catch breakage |

A local **"chaos site"** with deliberately awkward behavior is the best investment for the edge cases above.

---

## 10. Phased build plan

| Phase | Deliverable | Done when |
|---|---|---|
| 0. Setup | Repo, config, Pydantic settings, DB models, LLM wrapper | `ask()` returns validated JSON |
| 1. Site analysis | Fetcher with fallback, sitemap mapper, URL grouping | Prints a clean URL list for 10 diverse sites |
| 2. Page picking and screenshots | PagePicker, Screenshotter with quality checks | 3+ good screenshots on 8 of 10 sites |
| 3. Extraction | Fact schema, quote verification, gap-fill loop | Facts with sources for pricing and features |
| 4. Discovery and selection | Multi-source discovery, validation, dedupe, selection | Produces 2 valid products from any category |
| 5. Comparison and render | Structured comparison, verifier pass, HTML template | A readable report end to end |
| 6. Orchestration | Stage runner, resumability, retries, partial reports | Kill the process mid-run, restart, it finishes |
| 7. API and scheduler | FastAPI endpoints, background worker, daily schedule | One click or cron produces a report |
| 8. Hardening | Rate limits, SSRF guard, budget caps, alerts, Docker | Runs unattended for 7 days |
| 9. Optional UI | React frontend listing reports | Browse history and compare views |

**Tip:** test each stage as a standalone script before wiring them together.

---

## 11. Deployment

- **Docker** image based on the official Playwright Python image (browsers preinstalled)
- Two processes: `api` (uvicorn) and `worker` (pipeline + scheduler)
- Mount a volume (or S3) for screenshots and the SQLite file; move to Postgres once you have more than one worker
- Environment: `CATEGORY`, `ANTHROPIC_API_KEY`, `SEARCH_API_KEY`, `MAX_RUN_COST`, `MAX_RUN_MINUTES`
- Hosting options: small VPS, Fly.io, Railway, or Render (cron job + web service)

---

## 12. Top failure modes

1. **LLM invents a URL, fact or product.** Fix: membership checks and quote verification.
2. **Site blocks you.** Fix: fallback ladder, then substitute from the backup list.
3. **Useless screenshots (cookie walls, blanks).** Fix: pixel checks and vision validation.
4. **Run takes too long or costs too much.** Fix: budgets, caps, caching.
5. **Repeats or old incumbents.** Fix: domain-keyed dedupe and a newness score.
6. **Unfair comparison.** Fix: comparability score and coverage notes.
7. **Prompt injection from scraped pages.** Fix: delimiting, untrusted-data framing, no tool access from extraction prompts.

---

## 13. Autonomy checklist (nothing hardcoded)

| Decision | Made by |
|---|---|
| Category | Config / API parameter |
| Where to look for products | LLM-generated queries |
| Which products | LLM scoring + DB dedupe |
| Sitemap location | robots.txt → fallbacks → DOM crawl |
| Which pages matter | LLM ranking of discovered URLs |
| Which pages to screenshot | LLM flag per page |
| Comparison dimensions | Derived from facts actually found |
| When to dig deeper | Gap-filling loop on missing fields |
