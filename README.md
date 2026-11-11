# Local LLM Ultimate MCP Server

### Dynamic Tool Execution for Local AI Models

A comprehensive Model Context Protocol (MCP) server designed to supercharge local LLMs (like Gemma 4 via LM Studio). It equips local models with real-world execution capabilities, bridging the gap between isolated local inference and live data interaction.

---

### v2 Modular Platform (Phase 01 Core Foundation)

Ultimate MCP Server v2 provides a lightweight, profile-driven gateway built on the official MCP Python SDK v2 (`mcp>=2.3,<3`) with out-of-band human approvals for side effects, strict workspace path boundaries, and zero required API keys at startup.

#### Quick Start (v2 with `uv`)

1. **Install dependencies from lockfile:**
   ```bash
   uv sync --extra dev
   ```
2. **Run environment & tool diagnostics:**
   ```bash
   uv run ultimate-mcp doctor
   ```
3. **Start the stdio MCP server (default profile):**
   ```bash
   uv run ultimate-mcp serve --profile default
   ```
4. **Manage out-of-band approvals for write/sensitive operations:**
   ```bash
   uv run ultimate-mcp approvals list
   uv run ultimate-mcp approvals approve <approval_id>
   uv run ultimate-mcp approvals reject <approval_id>
   ```

See [`mcp.json.example`](mcp.json.example) for the LM Studio v2 host configuration and [`docs/CONFIGURATION.md`](docs/CONFIGURATION.md) for environment settings.

---

### Legacy Single-File Server (`ultimate_mcp_server.py`)

The legacy single-file server remains preserved in the repository root while tools are incrementally migrated to v2 modules.

#### Features & Capabilities

The server exposes multiple high-utility tools to the LLM:

- **Advanced Web Search:** Integrates Tavily API and DuckDuckGo for deep, context-aware web searching and real-time news retrieval.
- **Financial Market Data:** Real-time fetching of stock, crypto, and currency prices using `yfinance`.
- **YouTube Expert Analysis:** Automated video search, transcript extraction, and top-comment scraping via the official YouTube API.
- **Web Content Extraction:** Full webpage scraping and parsing for article summarization.
- **Local File System Access:** Secure reading of local TXT, PDF, and CSV files directly into the model's context window.
- **Utility Tools:** Live weather tracking, timezone-aware clocks, and dynamic mathematical evaluations.

---

### Tech Stack

| Component | Technology |
| :--- | :--- |
| Framework | Official MCP Python SDK v2 (`MCPServer`) / legacy FastMCP |
| Integrations | Tavily API, YouTube Data API v3, yfinance |
| Data Processing | Pandas, pypdf / PyPDF2, BeautifulSoup4 |

---

### Legacy Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone https://github.com/Mahmoud-Elmalky/Ultimate-MCP-Server.git
   cd Ultimate-MCP-Server
   ```

2. **Install dependencies:**
   ```bash
   pip install -r tests/characterization/requirements-baseline.txt
   ```

3. **Configure Environment Variables:**
Rename `.env.example` to `.env` and add your secure API keys:

   ```ini
   YOUTUBE_API_KEY=your_api_key
   TAVILY_API_KEY=your_api_key
   ```

4. **Run the Legacy MCP Server:**
   ```bash
   python ultimate_mcp_server.py
   ```

### Client Configuration (LM Studio / Claude Desktop)
To connect this server to your local LLM client, use [`mcp.json.example`](mcp.json.example) (v2) or the legacy configuration below:
   ```json
   {
     "mcpServers": {
       "GemmaUltimate": {
         "command": "python",
         "args": [
            "C:/path/to/your/ultimate_mcp_server.py"
         ]
       }
     }
   }
   ```
