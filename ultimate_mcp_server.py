import os, re, math, httpx, pytz, urllib.parse
import yfinance as yf
import pandas as pd
import PyPDF2
from datetime import datetime
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP
from googleapiclient.discovery import build
from youtube_transcript_api import YouTubeTranscriptApi
from tavily import TavilyClient
from duckduckgo_search import DDGS
from dotenv import load_dotenv

# Load environment variables securely
load_dotenv()

# 1. Server Initialization
mcp = FastMCP("GemmaUltimate_Tavily_V3")

# 2. Key Settings (Loaded from .env to prevent hardcoding)
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# Initialize Tavily client
tavily = TavilyClient(api_key=TAVILY_API_KEY)

# --- [Helper Functions] ---
def extract_video_id(url):
    match = re.search(r"(?:v=|\/|youtu\.be\/|shorts\/|embed\/)([0-9A-Za-z_-]{11})", str(url))
    return match.group(1) if match else None

# ---------------------------------------------------------
# Tool 1: Advanced Web Search (Tavily Search)
# ---------------------------------------------------------
@mcp.tool()
def web_search(query: str) -> str:
    """Powerful tool for searching up-to-date information, news, and highly accurate data from the internet."""
    try:
        response = tavily.search(query=query, search_depth="advanced", max_results=4)
        
        results = ["Professional Search Results:"]
        for res in response['results']:
            results.append(f"- {res['title']}\n{res['content']}\nURL: {res['url']}")
            
        return "\n---\n".join(results)
    except Exception as e:
        return f"Error in advanced search (check API Key): {e}"

# ---------------------------------------------------------
# Tool 2: Limited Web Search (No API required)
# ---------------------------------------------------------
@mcp.tool()
def limited_web_search(query: str, search_type: str = "auto") -> str:
    """
    Search for general information on the internet.
    - Use search_type='news' for current events, news, or today's updates.
    - Use search_type='general' for general and technical information.
    """
    try:
        results_text = "Recent Search Results:\n\n"
        
        with DDGS() as ddgs:
            if search_type == "news" or any(word in query.lower() for word in ["today", "news", "price", "match", "new", "latest"]):
                news_results = list(ddgs.news(query, max_results=4))
                if news_results:
                    results_text += "[News Section]:\n"
                    for r in news_results:
                        results_text += f"- {r['title']} ({r['date']})\n{r['body']}\nURL: {r['href']}\n\n"

            general_results = list(ddgs.text(query, max_results=3, timelimit="w"))
            if general_results:
                results_text += "[General Search - Last Week]:\n"
                for r in general_results:
                    results_text += f"- {r['title']}\n{r['body']}\nURL: {r['href']}\n\n"

        if len(results_text) < 30:
            return "Not enough recent information found, try altering the keywords."
            
        return results_text[:4000]
    except Exception as e:
        return f"Search engine error: {e}"

# ---------------------------------------------------------
# Tool 3: Financial Markets (Smart yfinance)
# ---------------------------------------------------------
@mcp.tool()
def get_market_price(symbol: str) -> str:
    """Fetch currency and gold prices. Examples: EGP=X (USD to EGP), BTC-USD (Bitcoin)."""
    try:
        symbol = str(symbol).upper().strip()
        if "-" in symbol and any(x in symbol for x in ["USD", "EUR"]) and not symbol.startswith(("BTC", "ETH", "SOL")):
            symbol = symbol.replace("-", "").replace("=X", "") + "=X"

        ticker = yf.Ticker(symbol)
        data = ticker.history(period="1d")
        if data.empty: return f"Cannot fetch price for symbol ({symbol})."
        return f"Live price for {symbol} is: {data['Close'].iloc[-1]:.2f}"
    except Exception as e: return f"Financial error: {e}"

# ---------------------------------------------------------
# Tool 4: Weather Status (Safe wttr.in)
# ---------------------------------------------------------
@mcp.tool()
def get_weather(location: str) -> str:
    """Check weather status for any city (e.g., Cairo, Badr City)."""
    try:
        safe_loc = urllib.parse.quote(str(location).strip())
        url = f"https://wttr.in/{safe_loc}?format=%l:+%C+%t+(Feels+like+%f)%0AHumidity:+%h%0AWind:+%w"
        res = httpx.get(url, timeout=10.0)
        return f"Current Weather:\n{res.text}" if res.status_code == 200 else "Weather server down."
    except Exception as e: return f"Weather error: {e}"

# ---------------------------------------------------------
# Tool 5: Web Scraper (Pro Web Scraper)
# ---------------------------------------------------------
@mcp.tool()
def read_article(url: str) -> str:
    """Read the full content of any URL for summarization."""
    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        response = httpx.get(url, headers=headers, timeout=15.0, follow_redirects=True)
        soup = BeautifulSoup(response.text, 'html.parser')
        text = "\n".join([p.get_text().strip() for p in soup.find_all('p') if p.get_text().strip()])
        return text[:4000] + "\n\n(Rest of content truncated)" if len(text) > 4000 else text
    except Exception as e: return f"Could not read the link: {e}"

# ---------------------------------------------------------
# Tool 6: Comprehensive YouTube Expert (Search + Analysis)
# ---------------------------------------------------------
@mcp.tool()
def youtube_expert(query_or_url: str, task: str = "auto") -> str:
    """Search YouTube videos, extract summaries, or analyze top comments."""
    try:
        vid_id = extract_video_id(query_or_url)
        if not vid_id or task == "search":
            yt = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
            req = yt.search().list(q=query_or_url, part="snippet", type="video", maxResults=3)
            res = req.execute()
            out = "YouTube Results:\n"
            for item in res.get("items", []):
                out += f"- {item['snippet']['title']}\nURL: https://youtu.be/{item['id']['videoId']}\n"
            return out

        v_id = vid_id
        if task == "comments":
            yt = build("youtube", "v3", developerKey=YOUTUBE_API_KEY)
            req = yt.commentThreads().list(part="snippet", videoId=v_id, maxResults=5)
            res = req.execute()
            return "Top Comments:\n" + "\n---\n".join([i['snippet']['topLevelComment']['snippet']['textOriginal'] for i in res['items']])
        
        ts = YouTubeTranscriptApi.get_transcript(v_id, languages=['ar', 'en'])
        return "Video Summary:\n" + " ".join([t['text'] for t in ts])[:4000]
    except Exception as e: return f"YouTube error: {e}"

# ---------------------------------------------------------
# Tool 7: Calculator and Clock
# ---------------------------------------------------------
@mcp.tool()
def calculate_or_time(action: str, value: str = "Africa/Cairo") -> str:
    """Perform math operations or get time/date information."""
    try:
        if action == "math":
            safe_dict = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
            return f"Result: {eval(str(value), {'__builtins__': None}, safe_dict)}"
        if action == "time":
            tz = pytz.timezone(str(value).strip())
            return f"Time in {value}: {datetime.now(tz).strftime('%Y-%m-%d %I:%M:%S %p')}"
    except Exception as e: return f"Error: {e}"

# ---------------------------------------------------------
# Tool 8: Local File Reader
# ---------------------------------------------------------
@mcp.tool()
def read_local_file(file_path: str) -> str:
    """Read local PDF, TXT, or CSV files."""
    path = os.path.normpath(str(file_path).strip("'\""))
    if not os.path.exists(path): return "File not found."
    try:
        ext = path.split('.')[-1].lower()
        if ext == 'txt':
            with open(path, 'r', encoding='utf-8') as f: return f.read()[:4000]
        elif ext == 'pdf':
            with open(path, 'rb') as f:
                pdf = PyPDF2.PdfReader(f)
                return "\n".join([p.extract_text() for p in pdf.pages[:5]])
        elif ext == 'csv':
            return pd.read_csv(path).head(50).to_string()
        return "Unsupported format."
    except Exception as e: return f"Read error: {e}"

if __name__ == "__main__":
    mcp.run()