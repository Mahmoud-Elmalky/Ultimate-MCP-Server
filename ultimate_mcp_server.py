import os, re, math, httpx, pytz, urllib.parse
import yfinance as yf
import pandas as pd
import PyPDF2
from datetime import datetime
from typing import Annotated, Literal
from pydantic import Field
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
def web_search(
    query: Annotated[str, Field(min_length=2, max_length=1000, description="Focused live-web search query. Include the key subject, location, version, date, or identifier needed to verify the answer.")]
) -> str:
    """
    PRIMARY LIVE WEB-RESEARCH TOOL.

    Search the live internet for current, factual, and verifiable information.

    USE THIS TOOL when:
    - the user asks for current, latest, recent, today's, now, or up-to-date information
    - facts may have changed since the model's knowledge cutoff
    - the question involves news, current events, software/AI releases or versions,
      APIs, products, services, laws, regulations, schedules, statistics, or availability
    - accurate external verification is important

    TOOL-USE RULE:
    Prefer this tool over the model's internal knowledge for information that may be outdated.
    For current-information questions, search before answering.
    If the first search is insufficient, ambiguous, or conflicting, perform another search
    with a more precise query.

    SEARCH QUALITY:
    Use the user's actual intent and include important names, versions, dates, locations,
    model names, product names, or other identifiers when relevant.
    Prefer authoritative and primary sources when possible.

    DO NOT use this tool as a substitute for a specialized tool when one below is clearly
    better suited, such as market prices, weather, YouTube content, or local files.

    Args:
        query: A focused natural-language search query containing the information that
               needs to be verified on the live web.
    """
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
def limited_web_search(
    query: Annotated[str, Field(min_length=2, max_length=1000, description="Focused search query for the user’s information need.")],
    search_type: Annotated[Literal["auto", "news", "general"], Field(description="Search mode: use news for current events/news, general for normal web research, or auto when the engine should choose.")] = "auto"
) -> str:
    """
    FALLBACK / SECONDARY LIVE WEB-SEARCH TOOL.

    Search the internet using DuckDuckGo when the primary web_search tool is unavailable,
    fails, or when an independent second search source is useful.

    USE search_type="news" for:
    - breaking news, current events, today's updates, recent developments

    USE search_type="general" for:
    - general web information, technical topics, documentation, and broader searches

    TOOL-USE RULE:
    For important current factual questions, prefer web_search first.
    Use this tool when a second independent search is useful or when the primary search
    cannot provide enough information.

    Args:
        query: Focused search query for the user's information need.
        search_type: "news", "general", or "auto". Use "news" for current events/news;
                     use "general" for regular web research.
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
def get_market_price(
    symbol: Annotated[str, Field(min_length=1, max_length=50, description="Yahoo Finance ticker or supported asset symbol, e.g. EGP=X, BTC-USD, AAPL.")]
) -> str:
    """
    REAL-TIME MARKET DATA TOOL.

    Fetch the latest available market price for supported currencies, gold, Bitcoin,
    stocks, and other supported Yahoo Finance assets.

    USE THIS TOOL INSTEAD OF WEB SEARCH when the user asks for a current/live/latest
    price or market quote that this tool supports.

    IMPORTANT:
    Do not estimate a live price from memory.
    Do not invent a price if the symbol cannot be fetched.
    The returned value is the tool's latest available market data and may reflect
    the market's current trading status.

    Examples:
    - EGP=X -> USD/EGP exchange rate
    - BTC-USD -> Bitcoin/USD
    - GOLD -> gold-related Yahoo Finance symbol, if supported
    - AAPL -> Apple stock

    Args:
        symbol: Yahoo Finance ticker or supported asset symbol.
    """
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
def get_weather(
    location: Annotated[str, Field(min_length=1, max_length=200, description="City, town, or geographic location whose CURRENT weather should be checked.")]
) -> str:
    """
    CURRENT WEATHER TOOL.

    Get the current weather conditions for a city or location.

    USE THIS TOOL for current weather questions such as:
    - current temperature
    - conditions now
    - feels-like temperature
    - humidity
    - wind

    TOOL-USE RULE:
    Do not answer a current-weather question from model memory when this tool is available.
    Use the live tool result as the factual basis of the response.

    Args:
        location: City, town, or location name to check.
    """
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
def read_article(
    url: Annotated[str, Field(min_length=8, max_length=4000, description="Full webpage or article URL to fetch and inspect. Use after a URL is known.")]
) -> str:
    """
    SPECIFIC-WEBPAGE READER.

    Fetch and extract readable text from a specific URL supplied by the user or discovered
    during web research.

    USE THIS TOOL when:
    - the user gives you a specific article/webpage URL and asks you to read, summarize,
      analyze, or quote its contents
    - a web-search result needs deeper inspection after discovery

    TOOL-USE RULE:
    Use web_search to DISCOVER relevant pages.
    Use read_article to INSPECT a known URL in detail.
    Do not treat this tool as a replacement for web search when you do not yet know which
    webpage contains the needed information.

    Args:
        url: Full URL of the webpage or article to read.
    """
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
def youtube_expert(
    query_or_url: Annotated[str, Field(min_length=1, max_length=1000, description="YouTube search query or full YouTube video URL.")],
    task: Annotated[Literal["auto", "search", "comments"], Field(description="Use search to find videos, comments to inspect top comments, or auto for a specific video URL/transcript.")] = "auto"
) -> str:
    """
    YOUTUBE-SPECIFIC RESEARCH TOOL.

    Search YouTube, retrieve a video's transcript, or inspect top comments.

    USE task="search" when:
    - the user asks you to find YouTube videos

    USE task="comments" when:
    - the user asks about comments, reactions, or top comments on a specific video

    USE task="auto" when:
    - a specific YouTube video URL is provided and the user needs its content/transcript

    TOOL-USE RULE:
    Use this tool when the answer specifically depends on YouTube content.
    Do not invent transcript text, comments, video titles, or URLs.

    Args:
        query_or_url: YouTube search query or a YouTube video URL.
        task: "search", "comments", or "auto".
    """
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
def calculate_or_time(
    action: Annotated[Literal["time", "math"], Field(description="Use time for the current date/time; use math for exact calculations.")],
    value: Annotated[str, Field(description="Timezone for time (default Africa/Cairo), or mathematical expression for math.")] = "Africa/Cairo"
) -> str:
    """
    CURRENT CLOCK / DATE AND MATH TOOL.

    Use action="time" to obtain the authoritative current date and time for a timezone.
    Use action="math" for numerical calculations.

    TIME/DATE RULE:
    Call action="time" when the answer depends on:
    - today, now, yesterday, tomorrow
    - current date or current time
    - this week, this month, this year
    - latest/recent timing or relative dates

    For current-information research, this tool should normally be used BEFORE web_search
    when relative dates could affect the search or interpretation.

    MATH RULE:
    Use action="math" for calculations instead of doing arithmetic mentally when exact
    numerical accuracy matters.

    Do not invent a current date/time. Use the returned tool value.

    Args:
        action: "time" for current date/time; "math" for calculations.
        value: Timezone name for action="time" (default Africa/Cairo), or the mathematical
               expression for action="math".
    """
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
def read_local_file(
    file_path: Annotated[str, Field(min_length=1, max_length=2000, description="Full local filesystem path to a PDF, TXT, or CSV file.")]
) -> str:
    """
    LOCAL FILE READER.

    Read the contents of a PDF, TXT, or CSV file available on the user's computer.

    USE THIS TOOL when:
    - the user asks about the contents of a local file
    - the answer must be extracted from a local PDF, TXT, or CSV
    - the user explicitly references a file path

    TOOL-USE RULE:
    Never invent file contents.
    Base file-specific answers on the text returned by this tool.
    If the file cannot be found, cannot be read, or the format is unsupported, state that
    clearly instead of guessing.

    Args:
        file_path: Full local path to the PDF, TXT, or CSV file.
    """
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
