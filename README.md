# Cognivex Product Research

A configurable FastAPI application that discovers recently launched software products, researches their public websites, verifies facts against page text, captures screenshots, and publishes evidence-linked Markdown and HTML comparisons. The app includes a browser dashboard and a separate daily scheduler worker.

## Requirements

- Python 3.10+ for local setup, or Docker and Docker Compose
- A Gemini API key; optionally add a Groq API key for text-call fallback if Gemini is rate limited
- Optional admin key if you want to protect run/category write actions

The default LLM setup uses Gemini Flash-Lite for query generation, candidate extraction, page picking, and fact extraction, and Gemini Flash for selection, comparison, and screenshot review. Tavily is the broad web search provider; public GitHub repository search adds open-source signals and supplements current-alternative discovery. Google PageSpeed Insights adds mobile Lighthouse technical SEO and performance audits. These are not keyword rankings, backlinks, or traffic estimates. Both enrichments are best-effort and can be disabled; see `.env.example` for optional tokens/keys. Hacker News discovery also uses public Show HN posts. Product Hunt is not connected.

## Quick start with Docker

1. Create the environment file:

   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and set at least:

   ```dotenv
   GEMINI_API_KEY=your-gemini-key
   GROQ_API_KEY=your-rotated-groq-key # optional fallback
   ADMIN_API_KEY=
   ```

3. Build and start the API and daily worker:

   ```bash
   docker compose up --build -d
   ```

4. Open [http://localhost:8000](http://localhost:8000), choose a category, and start a research run. Admin protection is off when `ADMIN_API_KEY` is blank; set it in `.env` and enter the matching value in **API access** to enable protection.

5. Follow progress from **Recent research runs**. Completed and partial reports appear under **Latest reports**. Use the report preview to read HTML or open its Markdown version.

The API serves the dashboard and reads/writes the SQLite database and screenshots on the `research_data` volume. The scheduler reads `SCHEDULE_CRON` in UTC and starts a run for every active category.

Stop the containers with:

```bash
docker compose down
```

Data remains in the named volume. To remove the data too, run `docker compose down -v`.

## Local development

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
playwright install --with-deps chromium
cp .env.example .env
```

Set `GEMINI_API_KEY` in `.env`; add `GROQ_API_KEY` to enable fallback on Gemini rate limits. The example config uses Hacker News search, which requires no API key. `ADMIN_API_KEY` is optional for local use. For broader discovery, set `SEARCH_PROVIDER=tavily` and add `SEARCH_API_KEY`:

```bash
uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000). To enable scheduled runs locally, open another terminal with the same environment and run:

```bash
python -m app.scheduler
```

## Using the dashboard

1. If `ADMIN_API_KEY` is configured, save it with **API access**. It stays in this browser's local storage and is sent only to this app as `X-Admin-API-Key`.
2. Use **Categories** to add a software category or pause/reactivate one. The default category comes from `CATEGORY`.
3. Choose an active category in **Start a comparison**, then start the run.
4. Open a run to inspect stage progress, recorded model decisions, reasoning, token use, cost, and errors.
5. Open a report to see a visual comparison, evidence benchmarks, source quotes, and the captured screenshots. Partial reports identify the stage that could not meet its requirements.

## API use

Set `ADMIN_KEY` to the configured admin key. The API is also documented at `/docs`.

```bash
curl -H "X-Admin-API-Key: $ADMIN_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"category":"AI meeting notes"}' \
  http://localhost:8000/runs
```

Main routes:

| Method | Route | Purpose |
|---|---|---|
| `GET` | `/health` | Health check |
| `GET` | `/categories` | List categories |
| `POST` | `/categories` | Add a category; admin key required |
| `PATCH` | `/categories/{id}` | Activate or pause a category; admin key required |
| `POST` | `/runs` | Queue a run; optional JSON `{ "category": "..." }`; admin key required |
| `GET` | `/runs` | List recent runs |
| `GET` | `/runs/{id}` | Run status, stages, decisions, costs and errors |
| `GET` | `/reports` | List reports |
| `GET` | `/reports/{id}` | HTML report |
| `GET` | `/reports/{id}?format=md` | Markdown report |

## Configuration

Settings can be set in `.env` or the process environment. See [`.env.example`](.env.example) for all variables. Common settings:

| Variable | What it controls |
|---|---|
| `CATEGORY` | Default software category |
| `LLM_PROVIDER` | Primary LLM backend: `gemini`, `groq`, or `anthropic` |
| `GEMINI_API_KEY` | Gemini API credential when `LLM_PROVIDER=gemini` |
| `GROQ_API_KEY` | Optional Groq API credential; used as fallback when Gemini returns HTTP 429 |
| `ANTHROPIC_API_KEY` | Claude API credential when `LLM_PROVIDER=anthropic` |
| `GROQ_FALLBACK_MODEL` | Groq text model used after Gemini rate limits |
| `GROQ_JSON_FALLBACK_MODEL` | Smaller Groq model retried after a Groq structured-output rejection |
| `GITHUB_SEARCH_ENABLED`, `GITHUB_TOKEN` | Enable public GitHub repository search; token is optional and raises authenticated API limits |
| `PAGESPEED_AUDIT_ENABLED`, `PAGESPEED_API_KEY` | Enable Google's mobile PageSpeed/Lighthouse SEO and performance audit; API key is optional but recommended for repeated automated use |
| `LLM_MODEL_FAST` | Gemini Flash-Lite model for bulk extraction and query generation |
| `LLM_MODEL_STRONG` | Gemini Flash model for selection and verification |
| `LLM_MODEL_VISION` | Vision model for screenshot review |
| `LLM_*_USD_PER_MILLION` | Token rates used for run-cost accounting |
| `SEARCH_PROVIDER`, `SEARCH_API_KEY` | Search backend (`hacker_news` free/no-key or `tavily`) |
| `ADMIN_API_KEY` | Optional protection for run creation and category changes; blank disables it |
| `DATABASE_URL` | SQLModel database URL; SQLite by default |
| `SITEMAP_FALLBACK_PATHS` | Comma-separated protocol sitemap fallback paths |
| `MAX_PAGES_PER_PRODUCT`, `MAX_FACT_CHARS_PER_PAGE`, `MIN_SCREENSHOTS` | Per-product research limits and maximum page text sent to each fact-extraction call (18,000 characters by default) |
| `MAX_RUN_COST_USD`, `MAX_RUN_MINUTES` | Per-run safety limits |

### GitHub and PageSpeed setup

GitHub search works without credentials, but unauthenticated search has a low shared rate limit. For recurring runs, create a fine-grained GitHub token limited to public repository read access and set `GITHUB_TOKEN` in `.env`. The token is sent only to `api.github.com` and is never included in run output.

PageSpeed Insights can be called without a key, but automated use should have a Google Cloud API key with the PageSpeed Insights API enabled and quota available. Put it in `PAGESPEED_API_KEY`. Set either `GITHUB_SEARCH_ENABLED=false` or `PAGESPEED_AUDIT_ENABLED=false` to skip that provider. Provider quota errors are shown in each report and do not fail the research pipeline.

These integrations add public repository metadata and mobile technical SEO/Lighthouse checks. They do not provide keyword-position, backlink, or search-traffic data.
| `SCHEDULE_CRON` | Daily worker schedule, interpreted in UTC |

The example config selects Gemini Flash-Lite for bulk text work and Gemini Flash for stronger text and vision tasks. Both model IDs are configurable in `.env`; Gemini model availability and quotas depend on your AI Studio account. Groq GPT-OSS is the optional text fallback; it does not review screenshots. If Gemini is rate limited during screenshot judging, deterministic image-integrity checks keep the capture stage moving and the run records that fallback. See Google's [Gemini model list](https://ai.google.dev/gemini-api/docs/models) and Groq's [model list and pricing](https://console.groq.com/docs/models).

## Development checks

```bash
python -m compileall -q app tests
pytest -q
```

Browser screenshots require the Chromium build matching the installed Playwright version. If the browser executable is missing after dependency changes, run `playwright install --with-deps chromium`; Docker uses the matching Playwright base image. API and scheduler logs go to standard output; Docker logs can be viewed with `docker compose logs -f api worker`.

## Current limitations

- Product Hunt is not connected. Hacker News mode only discovers Show HN posts; niche categories may not have two recent, verifiable launches even after the 180-day fallback.
- When no two products have verifiable launch dates, the app can compare current category alternatives and labels that scope clearly. It does not claim the alternatives are recent launches.
- Sitemap discovery falls back to configured paths and same-site homepage links; it does not crawl arbitrary deep link graphs.
- SQLite is the supported default. Production schema migrations, monitoring/alerts, and retention controls need deployment-specific work.
- Automated tests cover unit and injected-service behavior. Live Tavily/Gemini/Groq requests, target-site Chromium captures, and a completed six-stage report have been verified locally; Docker image build and deployment-specific behavior still need verification.
# sendro-cognivex
