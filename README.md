# Local LLM Ultimate MCP Server

### Dynamic Tool Execution for Local AI Models

A comprehensive Model Context Protocol (MCP) server designed to supercharge local LLMs (like Gemma 4 via LM Studio). It equips local models with real-world execution capabilities, bridging the gap between isolated local inference and live data interaction.

---

### Features & Capabilities

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
| Framework | FastMCP (Python) |
| Integrations | Tavily API, YouTube Data API v3, yfinance |
| Data Processing | Pandas, PyPDF2, BeautifulSoup4 |

---

### Installation & Setup

1. **Clone the repository:**
   ```bash
   git clone [https://github.com/Mahmoud-Elmalky/Ultimate-MCP-Server.git](https://github.com/Mahmoud-Elmalky/Ultimate-MCP-Server.git)
   cd Ultimate-MCP-Server