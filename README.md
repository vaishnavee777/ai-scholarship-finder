# AI Scholarship Finder

A Streamlit app that helps students in India find scholarships they may qualify for. It reads **official**
sources with a real browser (Playwright MCP), keeps a verbatim quote behind every extracted fact, evaluates
eligibility with deterministic Python rules, and uses Gemini only to *extract with quotes* and to *explain*.
Accuracy and source verification come before the number of results.

> **Status in one line:** live discovery, extraction, eligibility, red flags, ranking, storage and UI are implemented
> and tested against real official sites. **Gemini was tested only with a mocked client** (no API key was available
> during development) — see [What is tested](#what-is-implemented-tested-mocked-or-incomplete).

## Quick start

```bash
cd ai-scholarship-finder
python3 -m venv .venv && source .venv/bin/activate      # Python 3.11+ (developed on 3.11.9)
pip install -r requirements.txt
cp .env.example .env                                    # then add GEMINI_API_KEY (optional but recommended)
streamlit run app.py
```

Requirements: **Node.js + npx** (the app launches `@playwright/mcp` itself; first run downloads it and needs network)
and a Chromium that Playwright can use. Without `GEMINI_API_KEY` the app still works in a reduced mode (see below).
The sidebar's **Demo data** mode needs no network and shows clearly-labelled fictional records.

## Configuration (`.env`)

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | – | Enables Gemini extraction and explanations. Never logged or shown. |
| `GEMINI_MODEL` | `gemini-3.8-flash` | Any Gemini model id your key can use. |
| `MCP_SERVER_COMMAND` / `MCP_SERVER_ARGS` | `npx` / `-y @playwright/mcp@0.0.83 --headless --isolated` | How the Playwright MCP server is started. |
| `DB_PATH` | `data/scholarships.db` | SQLite file. |
| `MAX_PAGES_PER_SEARCH` | 8 | Hard cap on pages + PDFs read per search. |
| `MAX_FOLLOW_LINKS_PER_PAGE` | 3 | Guideline / FAQ links followed per page. |
| `PAGE_TIMEOUT_SECONDS` | 45 | Per navigation / download. |
| `CACHE_TTL_HOURS` | 24 | Reuse of a previous crawl. “Force fresh verification” bypasses it. |
| `FRESHNESS_DAYS` | 14 | Evidence older than this cannot support “Applications open”. |
| `TRUSTED_PROVIDER_DOMAINS` | – | Comma-separated private/corporate domains *you* have verified as official. |
| `EXTRA_SEED_URLS` | – | Comma-separated official pages (state portals, university pages) always read first. |

## How Playwright MCP is actually connected

The Python app owns its browser server. `services/playwright_mcp_client.py` uses the official Python `mcp` client
over **stdio** to spawn `npx @playwright/mcp@…` and calls its real tools:

* `browser_navigate` – open the URL
* `browser_wait_for` – short settle time for script-rendered pages
* `browser_evaluate` – returns visible text, title, final URL and links (with nearby row text for ranking)

At connect time the client lists the server's tools and refuses to run if the required ones are missing.
**It is not connected to Claude Code's MCP configuration** (none was configured on the development machine, and a
Claude Code MCP server isn't callable from a separate Python process anyway). If Node/npx or the server is
unavailable, the search reports that clearly and returns no results rather than faking any.
Only the stdio transport is implemented (HTTP/SSE transport is not).

**PDFs:** the browser returns no text for PDFs, and NSP publishes eligibility rules as PDFs, so
`services/pdf_fetcher.py` downloads them over HTTPS with the same safety checks and extracts text with `pypdf`.
Scanned (image-only) PDFs are reported as unreadable.

## Pipeline

```
profile ─► queries (DuckDuckGo via ddgs: discovery only) + seeds (NSP scheme list, EXTRA_SEED_URLS)
        ─► official domains first ─► Playwright MCP / PDF fetch (robots.txt, SSRF checks, page + retry limits)
        ─► follow guideline / eligibility / FAQ links, ranked by relevance to the profile
        ─► extraction: Gemini (JSON schema + verbatim quotes)  — or the rule-based fallback
        ─► normalizer: keep a value only if its quote is in the page AND contains that value
        ─► dedupe / conflict detection ─► SQLite
        ─► eligibility engine ─► red flags ─► ranking ─► dashboard ─► (optional) Gemini explanation
```

### Trust model

* **Quote verification** (`scholarship_normalizer.py`): every extracted amount, date, number, category, etc. must be
  backed by a quote that is a verbatim substring of the fetched text *and* contains the value (₹8 lakh ↔ 800000,
  `31-10-2026` ↔ 2026-10-31, “SC” only as a standalone abbreviation). Anything else is dropped and listed as
  unresolved. Gemini's output is never trusted on its own.
* **Source classification** (`source_validator.py`): `*.gov.in`/`*.nic.in` → government; `*.ac.in`/`*.edu.in` →
  institution; your allow-list → provider; known aggregators → third-party; everything else → **unverified**.
  Lookalike hosts (`evilgov.in`, `scholarships.gov.in.evil.com`) are not official. Unverified/third-party sources
  always yield the **Unverified** status.
* **Eligibility is deterministic** (`eligibility_engine.py`). Gemini receives the result and cannot change it.
* **“Open” needs recent evidence**: a future deadline alone gives *Unknown* unless the page was verified within
  `FRESHNESS_DAYS`. A passed deadline gives *Closed* (extensions are possible; the UI says so).

### Eligibility statuses

`ELIGIBLE` all evaluated mandatory criteria met · `POTENTIAL_MATCH` nothing failed but something is unknown ·
`LIKELY_INELIGIBLE` a verified criterion failed · `UNVERIFIED` source unofficial, outdated (older academic year
than the current one, which rolls over in June), stale (>180 days) or without criteria. Application status
(Open / Upcoming / Closed / Unknown) is separate.

Design choices worth knowing:
* A core criterion the extractor could not find (income, category, state, level, minimum marks) is **UNKNOWN**, not
  “no restriction”, so records cannot become ELIGIBLE on fields nobody read. The one heuristic: in a full guideline
  PDF where ≥3 other core criteria were found, silence about minimum marks is treated as “none stated”.
* Percentage and CGPA are never converted into each other. Income *ranges* give MET/NOT_MET only when the whole
  range is on one side of the limit.
* Course matching only says NOT_MET when both sides are recognised course families that don't overlap (B.E. vs MBBS);
  unfamiliar names such as a branch give UNKNOWN. Institution matching is fuzzy and the message says to check spelling.
* Award counts are shown only with the scope the source gave. Nothing says “only N in India” unless the source says
  so; otherwise the UI says **Number of awards not publicly verified.** No acceptance rates or probabilities exist anywhere.

## Privacy & safety

* No names, Aadhaar, bank details or documents are collected; the profile lives only in the Streamlit session.
* The search cache key uses course/level/state/category/institution/academic year only (not income or marks).
  Search history is opt-in and stores a coarse text summary. “Clear saved searches & AI cache” wipes caches/history.
* Browser and PDF fetches: http(s) only, ports 80/443, no credentials in URLs, hostnames must resolve to public
  addresses, redirects are re-validated (PDFs hop by hop; browser final URL after navigation, content discarded if unsafe),
  robots.txt is honoured (4xx → allowed, 5xx/unreachable → skipped), one page at a time, bounded size/time/retries.
  No CAPTCHA, login or anti-bot bypass. Scraped text is treated as data in prompts; all scraped text shown in the UI
  is escaped (HTML and markdown) and non-http(s) links are dropped.
* Known gap: DNS can change between our check and the browser's own lookup (DNS rebinding); the post-navigation
  URL re-check narrows but does not eliminate this. Run the app on a network without access to internal services.

## Tests

```bash
pytest -q                       # 175 offline tests
RUN_LIVE=1 pytest tests/test_live.py -q   # real Playwright MCP + real scholarships.gov.in (needs network/Node)
```

## What is implemented, tested, mocked, or incomplete

| Area | State |
|---|---|
| Pydantic models, SQLite repository (WAL, parameterised SQL, concurrent-write test) | Implemented, unit-tested |
| Eligibility engine (income/marks boundaries, category, state, course, level, year, institution, gender, disability, fresh/renewal, conflicts, deadlines) | Implemented, ~50 unit tests |
| Red flags, ranking, filters | Implemented, unit-tested |
| Source validator (SSRF, classification, robots) | Implemented, unit-tested (DNS/HTTP mocked in tests) |
| Playwright MCP client | Implemented; **verified live** against the real server and scholarships.gov.in. Retry/redirect/block logic is tested with a *fake MCP session* (mock) |
| PDF fetcher | Implemented; verified live on a real NSP guideline PDF |
| Live search end-to-end | **Verified live** (rule-based extraction, no Gemini): real AICTE schemes, income limit ₹8 lakh verified from the PDF, ₹1.5L passes and ₹12L fails, robots-blocked site skipped and reported, cache hit works. Orchestrator logic otherwise tested with fake browser/searcher/Gemini (mocks) |
| Gemini extraction & summaries | Implemented against the installed `google-genai` SDK; schemas verified to convert for the SDK. **Not exercised against the real API** (no key during development): tested with a fake client for validation, retries, error mapping, caching, key-leak and grounding checks. Prompt quality is therefore unproven |
| Streamlit UI | Implemented; driven in a real browser (demo and live), desktop and 390px mobile; Streamlit AppTest flows; hostile-content test |
| Demo data | 8 **fictional** records (`data/sample_scholarships.json`), always labelled DEMO, never mixed into live results |

**Incomplete / limitations**
* `services.india.gov.in` (requested as a source) does **not resolve** (NXDOMAIN, checked 2026-10-28), so it is not a default
  seed; add working pages via `EXTRA_SEED_URLS`. State portals and university pages are reached only through search results or `EXTRA_SEED_URLS`.
* Without a Gemini key, extraction is rule-based and **PDF-only** (income limit, minimum %, a single unambiguous deadline,
  academic year). Listing pages such as the Karnataka SSP list several departments with different deadlines, so they are
  intentionally not read by rules. Expect mostly *Potential match* results in this mode.
* Discovery depends on DuckDuckGo via `ddgs`, which sometimes rate-limits; seeds still run.
* Scheme de-duplication is name-based (token overlap); different names for one scheme may stay separate.
* Pages needing login, CAPTCHA or heavy interaction are not handled (and must not be bypassed). Multi-step portal forms are not read.
* No authentication/multi-user support; SQLite is for a single machine.

## Deployment

* **Local / single server:** `streamlit run app.py --server.port 8501` behind a reverse proxy. Needs Node.js and
  Chromium on the host (`npx playwright install chromium` if the server can't find a browser). Set `GEMINI_API_KEY`
  through the host's secret store, not a committed file.
* **Container:** base on a Python image with Node 20+ and Playwright's browser dependencies; mount a volume for `data/`.
* Streamlit Community Cloud is **not** suitable as-is (no Node/Chromium guarantee, ephemeral disk).
* Keep the host's network egress to the public internet only (see the DNS rebinding note).

## Layout

`app.py` · `config/settings.py` · `models/{student,scholarship,evaluation}.py` ·
`services/{search_orchestrator,playwright_mcp_client,pdf_fetcher,page_extractor,scholarship_normalizer,source_validator,eligibility_engine,red_flag_detector,ranking_service,gemini_service,demo_loader}.py` ·
`database/{db,repository}.py` · `ui/{profile_form,results,scholarship_card,red_flags,ai_summary,styles}.py` · `tests/` · `data/sample_scholarships.json`
