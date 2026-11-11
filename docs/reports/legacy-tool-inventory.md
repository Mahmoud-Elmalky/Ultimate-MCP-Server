# Legacy Tool Inventory (Phase 0)

Source: `ultimate_mcp_server.py` at commit `aafcce6` (origin/main). Behavior below is pinned by
`tests/characterization/` (56 tests, all offline). Environment: `tests/characterization/requirements-baseline.txt`
(mirrors the user's live interpreter used by LM Studio).

Common traits of **all** tools:

- Synchronous `def` handlers registered with `FastMCP.tool()` (MCP SDK 1.x API).
- Return plain `str`. Every failure is caught by `except Exception` and returned as **successful**
  text (`isError=false`) — the host/model cannot distinguish errors from data.
- No provenance (provider, retrieval time) and no freshness metadata.
- Output caps are hard-coded `4000` chars; most truncate **silently**.
- Raw exception messages are returned to the model (may expose internal details).

| # | Tool | Inputs (schema) | Provider / deps | Side effects | Output / limits | Error behavior | Decision |
|---|---|---|---|---|---|---|---|
| 1 | `web_search` | `query: str` (2–1000) | Tavily (`tavily-python`), client built **at import** | Outbound HTTPS to Tavily | `"Professional Search Results:"` + 4 results joined by `\n---\n`; `search_depth="advanced"`; no cap on content length | `"Error in advanced search (check API Key): {e}"` | **Preserve name**; Tavily provider adapter, lazy init |
| 2 | `limited_web_search` | `query: str` (2–1000), `search_type: auto\|news\|general` = `auto` | DuckDuckGo (`duckduckgo-search` 8.1.1 — **renamed to `ddgs`**, emits RuntimeWarning) | Outbound HTTPS | News section (4) triggered by `search_type="news"` **or** keywords (`today, news, price, match, new, latest`) even when `general`; text results (3, last week). Silent `[:4000]` cut | `"Search engine error: {e}"`; `"Not enough recent information found…"` when empty | **Preserve as compatibility wrapper**; migrate to `ddgs` provider |
| 3 | `get_market_price` | `symbol: str` (1–50) | `yfinance` (+ pandas, numpy) | Outbound HTTPS to Yahoo | `"Live price for {SYM} is: {close:.2f}"` — no currency, timestamp, or market status; misleading "Live" | `"Cannot fetch price for symbol ({SYM})."`; `"Financial error: {e}"` | **Preserve name**; remove heuristic rewriting (`usd-egp`→`USDEGP=X`), add freshness |
| 4 | `get_weather` | `location: str` (1–200) | wttr.in via `httpx` (timeout 10 s) | Outbound HTTPS | `"Current Weather:\n{text}"` (wttr format string). Response body unbounded | Non-200 → `"Weather server down."`; `"Weather error: {e}"` | **Preserve name**; provider adapter, parsed fields, units, observation time |
| 5 | `read_article` | `url: str` (8–4000) | `httpx` + BeautifulSoup | Outbound HTTP(S) to **any** URL | `<p>` text only; `[:4000]` + `"(Rest of content truncated)"` | Ignores HTTP status (404 page body returned as content); `"Could not read the link: {e}"` | **Replace** with `read_webpage`; keep wrapper. SSRF-hardened fetch |
| 6 | `youtube_expert` | `query_or_url: str` (1–1000), `task: auto\|search\|comments` = `auto` | YouTube Data API v3 (`google-api-python-client`), `youtube-transcript-api` | Outbound HTTPS | Search: 3 results; comments: 5 top-level; transcript: `[:4000]` silent | `"YouTube error: {e}"`. **Transcript path is broken**: `YouTubeTranscriptApi.get_transcript` no longer exists in 1.2.x | **Split** → `youtube_search`, `youtube_transcript`, `youtube_comments`; keep adapter |
| 7 | `calculate_or_time` | `action: time\|math` (required), `value: str` = `"Africa/Cairo"` | `math`, `pytz` | None | `"Result: {v}"` / `"Time in {tz}: %Y-%m-%d %I:%M:%S %p"` | `"Error: {e}"` (unknown tz → `"Error: 'Mars/Phobos'"`). `action="math"` with default value → error | **Split** → `calculate` (AST allowlist) + `get_current_time` |
| 8 | `read_local_file` | `file_path: str` (1–2000) | `PyPDF2` (deprecated), `pandas` | Reads **any** local path | TXT `[:4000]` silent; PDF first 5 pages (no cap on text); CSV `head(50)` | `"File not found."`, `"Unsupported format."`, `"Read error: {e}"` | **Replace** with workspace-scoped `document_read`; keep wrapper |

## Tool-definition footprint

Measured from `mcp.list_tools()` (name + description + inputSchema, JSON): **10,134 chars ≈ 2.5k tokens**
(chars/4 heuristic; no model tokenizer used). Descriptions are long, multi-paragraph guidance blocks.

## Dependency inventory

`requirements.txt` is fully **unpinned**. A fresh install on 2026-11-11 resolved `mcp==2.3.0`, under which the
server fails to import (`ModuleNotFoundError: No module named 'mcp.server.fastmcp'`).

| Package | User live env (LM Studio) | Fresh unpinned resolve | Notes |
|---|---|---|---|
| mcp | 1.27.0 | 2.3.0 | v2 renamed `FastMCP` → `MCPServer`; legacy import breaks |
| youtube-transcript-api | 1.2.4 | 1.2.5 | static `get_transcript` removed; instance `fetch` API |
| duckduckgo-search | 8.1.1 | 8.1.1 | renamed to `ddgs` |
| tavily-python | 0.7.23 | 0.8.5 | raises on missing key at client construction |
| yfinance | 1.0 | 1.8.0 | pulls pandas/numpy/curl_cffi |
| pandas | 2.3.3 | 3.1.1 | heavy; used only for CSV head |
| PyPDF2 | 3.0.1 | 3.0.1 | deprecated → `pypdf` |
| httpx | 0.28.1 | 0.28.1 | |
| beautifulsoup4 | 4.14.3 | 4.15.0 | |
| google-api-python-client | 2.194.0 | 2.202.0 | heavy import |
| pytz | 2025.2 | 2026.6 | replace with stdlib `zoneinfo` (+`tzdata` on Windows) |
| python-dotenv | 1.2.1 | 1.2.4 | |
| pydantic | 2.12.5 | 2.14.3 | transitive via mcp |
