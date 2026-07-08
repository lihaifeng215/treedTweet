# TrendArticle · 热点公众号文章生成器

Single-page web app that aggregates **real** trending topics from multiple sources and generates WeChat public account (公众号) hot articles via a **configured LLM** (OpenAI-compatible).

## Stack
- **Backend**: Python Flask (`backend/`) — `app.py` (routes + static hosting), `config.py` (LLM config), `fetchers.py` (real data fetching), `llm_client.py` (OpenAI-compatible LLM client)
- **Frontend**: Vanilla HTML + CSS + JS (`frontend/`) — no framework, no build step
- **Data sources**: Hacker News API, B站热门, 百度热搜, 头条热榜, 抖音热榜, GitHub Trending, 微博热搜 (all live, no mock data)
- **LLM**: OpenAI-compatible `/chat/completions` (OpenAI / DeepSeek / Ollama / etc.)

## Run
```bash
pip install -r requirements.txt   # flask, requests
python backend/app.py             # http://localhost:5000  (settings: /settings)
```
1. Open `http://localhost:5000/settings`, configure API Key + Base URL + Model, click **测试连接** then **保存配置**.
2. On the home page click **刷新热点** to fetch live data.
3. Click **生成文章** on any card (choose a style) or **分析** for LLM-based monitoring analysis.

## Configure LLM
- UI: `/settings` page (persisted to `backend/config.json`, gitignored).
- Env vars (preferred for production): `LLM_API_KEY`, `LLM_BASE_URL`, `LLM_MODEL`, `LLM_TEMPERATURE`, `LLM_ARTICLE_MAX_LENGTH`.

## Architecture
### Backend — `backend/`
- **`app.py`** — Flask routes:
  - `GET /api/hotspots` — concurrent fetch of all sources, sorted by `engagement`, top 30, plus `llm_configured` flag.
  - `POST /api/generate-tweet` — LLM generates a full public account article (title + content) for a hotspot + style.
  - `POST /api/analyze-hotspot` — LLM produces structured monitoring data (summary/key_points/sentiment/topics/trend/audience/relevance).
  - `GET|POST /api/llm-config` — read/update LLM config; `POST /api/test-llm` — connection test.
- **`config.py`** — config priority: defaults < `config.json` < env vars; `is_configured()`.
- **`fetchers.py`** — `fetch_with_timeout` (threaded), 7 real fetchers, `fetch_all()` (concurrent). Returns `[]` on failure, never mock data.
- **`llm_client.py`** — OpenAI-compatible client. `STYLES` (in_depth/sharp/practical/story) with Chinese prompts for 公众号 articles. `monitor_hotspot()` + `generate_article()` parse JSON defensively.

### Frontend — `frontend/`
- `index.html` — nav (刷新/生成/设置), stats, filter tabs, hotspot cards (生成文章/分析/复制标题), article panel, analysis panel.
- `app.js` — `allHotspots`/`selectedHotspotId`/`currentFilter`/`llmConfigured`. `generateForHotspot` guards on `llmConfigured`; renders article with title + full content.
- `settings.html` / `settings.js` / `settings.css` — LLM config UI with save + test, article max length setting.

## Key conventions
- Hotspot fields: `title`, `source`, `type` (tech/general/trending), `engagement`, plus source-specific (`points`/`upvotes`/`comments`/`heat`/`desc`/`body`). Frontend renders whatever is present.
- `engagement` per source: HN `points+comments*5`, Bilibili `view+like*2+reply*3`, Baidu `hotScore`, Toutiao `HotValue`, Douyin `hot_value`, GitHub `stars*5+forks*3`, Weibo `hot_num`.
- Articles are LLM-generated with a catchy title (10-30 chars) and full body content (~2000 chars default, configurable).
- Chinese UI/prompts/comments throughout.
