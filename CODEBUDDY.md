# CODEBUDDY.md This file provides guidance to CodeBuddy when working with code in this repository.

## Common Commands

**Install dependencies:**
```bash
pip install -r requirements.txt
```

**Start the development server:**
```bash
python backend/app.py
```
Runs Flask on `http://0.0.0.0:5000` with debug mode enabled. The frontend is served by Flask as static files from the `frontend/` directory. Settings page at `http://localhost:5000/settings`.

**No test/lint commands are configured.** This project has no test suite or linter setup.

## Architecture

TrendArticle is a web app for aggregating real-time hot topics and generating WeChat public account (公众号) articles via a **渐进式创作工作流 (progressive 6-step workflow)**. The homepage is a three-column workflow UI; the hotspot list is a separate "素材库" (library) page. Backend: Flask + SQLite; no build step.

### Backend (`backend/`)

- **`app.py`** — Flask app. `static_folder` = `frontend/`, `static_url_path` = `''`. Routes:
  - `GET /` → `workflow.html` (三栏工作流主页); `GET /library` → `library.html` (素材库); `GET /settings` → `settings.html`
  - **Workflow API** (v6.0): `POST /api/workflow/create`, `GET /api/workflow/<id>`, `GET /api/workflows`, `DELETE /api/workflow/<id>`, `POST /api/workflow/<id>/{parse,research,topics,topic/select,outline,outline/confirm,generate,verify,export}`. The `generate` endpoint is SSE-streamed.
  - Legacy: `GET /api/hotspots` (top 50, with TTL cache + auto-retry), `POST /api/generate-tweet` (one-shot article, SSE-capable), `POST /api/analyze-hotspot`, `GET|POST /api/llm-config`, `POST /api/test-llm`, export endpoints.
- **`workflow_engine.py`** — Workflow state machine + SQLite persistence. Tables: `workflows`, `workflow_steps`. 6 steps: `parse → research → topics → outline → generate → verify`. Steps `topics`/`outline`/`verify` require user intervention (`waiting_user` state). DB at `backend/workflows.db`.
- **`search_client.py`** — Real search API client. `search(query, num)` returns `[{title,url,snippet}]`. Providers: DuckDuckGo (default, HTML parse, no key), SerpAPI, Bing. 5s timeout, returns `[]` on failure.
- **`config.py`** — Config management. Priority: defaults < `config.json` < env vars. LLM fields + search fields (`search_provider`, `search_api_key`, `search_base_url` via `SEARCH_PROVIDER`/`SEARCH_API_KEY`/`SEARCH_BASE_URL`).
- **`fetchers.py`** — Real data fetching (no mock fallbacks). 14+ sources, concurrent. Each returns `[]` on failure.
- **`llm_client.py`** — OpenAI-compatible client (`/chat/completions`). 4 article styles (professional_depth/humorous/suspenseful/emotional). Legacy methods: `generate_article()`, `generate_article_stream()`, `monitor_hotspot()`. **Workflow methods**: `parse_material()`, `generate_search_plan()`, `generate_topics()`, `generate_outline()`, `generate_body_stream()` (SSE), `verify_article()`. Uses `requests` directly (no openai SDK).

### Frontend (`frontend/`)

- **`workflow.html` + `workflow.js` + `workflow.css`** — Three-column workflow homepage (left: 创作参数; center: 6-step progress bar + step content + actions; right: 创作明细 accordion). Auto-runs parse→research→topics; user selects topic, confirms/edits outline, watches SSE body generation, reviews verify report, exports Markdown. Accepts `?hotspot=` param from library.
- **`library.html` + `library.js`** — 素材库 page (原热点列表). Stats bar, source/category filters, hotspot cards with "开始创作" button → redirects to workflow with hotspot data.
- **`settings.html` + `settings.js` + `settings.css`** — LLM config page + 搜索引擎配置区块 (provider dropdown / API key / base URL).
- **`index.html` + `app.js`** — Legacy one-shot generation page (no longer served at `/`).
- **`style.css`** — Shared dark theme (Linear/Vercel style, CSS variables).

### Key design notes

- The app is in Chinese (UI text, prompts, comments).
- Workflow state persists to SQLite (`workflows.db`); supports pause/resume across restarts.
- Search research uses real APIs (DuckDuckGo default, free); failures degrade gracefully (empty results, no crash).
- All hotspot data is fetched live from real public APIs; no mock data.
- LLM config is persisted to `backend/config.json` (gitignored) and settable via env vars.
- Flask runs with `debug=False` in production (`app.run`); `JSON_AS_ASCII=False` for UTF-8 Chinese.
