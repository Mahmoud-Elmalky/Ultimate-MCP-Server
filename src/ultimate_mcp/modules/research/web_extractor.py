"""SSRF-hardened webpage extraction, link/table extraction, bounded crawling, and RSS feeds.

Follows ``phases/PHASE-02_RESEARCH_UTILITIES.md`` §C and ``docs/SECURITY_MODEL.md`` §5:
- All network calls go through ``fetch_safe_url`` (SSRF/DNS/redirect/size guards).
- Never silently drops tail content; uses offset pagination or local artifact spillover.
- Parses RSS/Atom XML safely after rejecting ``<!DOCTYPE`` / ``<!ENTITY`` declarations
  to prevent XML external entity (XXE) or Billion Laughs expansion.
"""

from __future__ import annotations

import importlib
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Any

from ultimate_mcp.core.http import SafeHttpResponse, fetch_safe_url
from ultimate_mcp.core.models import (
    ErrorCode,
    Provenance,
    ResultStatus,
    ToolResult,
    UltimateMCPError,
)
from ultimate_mcp.providers.base import RSSFeedItem

DEFAULT_PAGE_CHAR_LIMIT = 6_000
MAX_EXTRACTED_LINKS = 100
MAX_EXTRACTED_TABLES = 10
MAX_TABLE_ROWS = 40
MAX_CRAWL_PAGES = 5
MAX_CRAWL_DEPTH = 2
MAX_CRAWL_DURATION_SECONDS = 15.0

PageFetcher = Callable[[str], SafeHttpResponse]


@dataclass(frozen=True)
class ExtractedWebpage:
    """Structured content extracted from an HTML or plain-text response."""

    requested_url: str
    final_url: str
    canonical_url: str
    title: str
    text: str
    total_chars: int
    start_offset: int
    end_offset: int
    truncated: bool
    next_offset: int | None
    retrieved_at: str


@dataclass
class _ParsedHTMLDoc:
    title: str = ""
    canonical_href: str = ""
    blocks: list[str] = field(default_factory=list)
    all_text_parts: list[str] = field(default_factory=list)
    links: list[tuple[str, str]] = field(default_factory=list)
    tables: list[tuple[str, list[list[str]]]] = field(default_factory=list)


class _StdlibHTMLParser(HTMLParser):
    """Lightweight stdlib HTML parser fallback when `beautifulsoup4` is not installed."""

    _IGNORED_TAGS = {"script", "style", "noscript", "svg", "iframe", "template"}
    _BLOCK_TAGS = {"h1", "h2", "h3", "p", "li", "pre", "blockquote"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = _ParsedHTMLDoc()
        self._ignore_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._block_stack: list[list[str]] = []
        self._current_href: str | None = None
        self._anchor_parts: list[str] = []
        self._in_table = False
        self._table_caption = ""
        self._in_caption = False
        self._caption_parts: list[str] = []
        self._table_rows: list[list[str]] = []
        self._current_row: list[str] | None = None
        self._in_cell = False
        self._cell_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        t = tag.lower()
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if t in self._IGNORED_TAGS:
            self._ignore_depth += 1
            return
        if self._ignore_depth > 0:
            return
        if t == "title":
            self._in_title = True
            self._title_parts = []
        elif t == "link":
            rel = attr_map.get("rel", "").lower()
            href = attr_map.get("href", "").strip()
            if "canonical" in rel and href and not self.doc.canonical_href:
                self.doc.canonical_href = href
        elif t in self._BLOCK_TAGS:
            self._block_stack.append([])
        elif t == "a" and "href" in attr_map:
            self._current_href = attr_map["href"]
            self._anchor_parts = []
        elif t == "table":
            self._in_table = True
            self._table_caption = ""
            self._table_rows = []
        elif t == "caption" and self._in_table:
            self._in_caption = True
            self._caption_parts = []
        elif t == "tr" and self._in_table:
            self._current_row = []
        elif t in ("th", "td") and self._current_row is not None:
            self._in_cell = True
            self._cell_parts = []

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in self._IGNORED_TAGS:
            if self._ignore_depth > 0:
                self._ignore_depth -= 1
            return
        if self._ignore_depth > 0:
            return
        if t == "title" and self._in_title:
            self._in_title = False
            self.doc.title = " ".join("".join(self._title_parts).split())
        elif t in self._BLOCK_TAGS and self._block_stack:
            raw_blk = " ".join("".join(self._block_stack.pop()).split())
            if raw_blk and (not self.doc.blocks or self.doc.blocks[-1] != raw_blk):
                self.doc.blocks.append(raw_blk)
        elif t == "a" and self._current_href is not None:
            anchor_txt = " ".join("".join(self._anchor_parts).split())
            self.doc.links.append((self._current_href, anchor_txt))
            self._current_href = None
        elif t in ("th", "td") and self._in_cell and self._current_row is not None:
            self._in_cell = False
            cell_txt = " ".join("".join(self._cell_parts).split())[:300]
            self._current_row.append(cell_txt)
        elif t == "tr" and self._current_row is not None:
            if any(self._current_row):
                self._table_rows.append(self._current_row)
            self._current_row = None
        elif t == "caption" and self._in_caption:
            self._in_caption = False
            self._table_caption = " ".join("".join(self._caption_parts).split())
        elif t == "table" and self._in_table:
            self._in_table = False
            self.doc.tables.append((self._table_caption, self._table_rows))

    def handle_data(self, data: str) -> None:
        if self._ignore_depth > 0:
            return
        if self._in_title:
            self._title_parts.append(data)
            return
        self.doc.all_text_parts.append(data)
        if self._block_stack:
            self._block_stack[-1].append(data)
        if self._current_href is not None:
            self._anchor_parts.append(data)
        if self._in_caption:
            self._caption_parts.append(data)
        if self._in_cell:
            self._cell_parts.append(data)


def _parse_html_document(html_text: str) -> _ParsedHTMLDoc:
    try:
        bs4: Any = importlib.import_module("bs4")
    except ImportError:
        parser = _StdlibHTMLParser()
        parser.feed(html_text)
        return parser.doc

    soup = bs4.BeautifulSoup(html_text, "html.parser")
    doc = _ParsedHTMLDoc()
    if soup.title and soup.title.string:
        doc.title = " ".join(str(soup.title.string).split())

    canon_tag = soup.find("link", rel=lambda v: v and "canonical" in str(v).lower())
    if canon_tag and canon_tag.get("href"):
        doc.canonical_href = str(canon_tag["href"]).strip()

    for a_tag in soup.find_all("a", href=True):
        href = str(a_tag.get("href") or "")
        txt = " ".join(a_tag.get_text(" ", strip=True).split())
        doc.links.append((href, txt))

    for tbl in soup.find_all("table"):
        cap_tag = tbl.find("caption")
        cap = " ".join(cap_tag.get_text(" ", strip=True).split()) if cap_tag else ""
        rows: list[list[str]] = []
        for tr in tbl.find_all("tr"):
            cells = [
                " ".join(c.get_text(" ", strip=True).split())[:300]
                for c in tr.find_all(["th", "td"])
            ]
            if any(cells):
                rows.append(cells)
        doc.tables.append((cap, rows))

    for tag in soup(["script", "style", "noscript", "svg", "iframe", "template"]):
        tag.decompose()

    for elem in soup.find_all(["h1", "h2", "h3", "p", "li", "pre", "blockquote"]):
        txt = " ".join(elem.get_text(" ", strip=True).split())
        if txt and (not doc.blocks or doc.blocks[-1] != txt):
            doc.blocks.append(txt)

    if not doc.blocks:
        fallback = " ".join(soup.get_text(" ", strip=True).split())
        if fallback:
            doc.blocks.append(fallback)
    return doc


def parse_readable_html(
    html_text: str,
    final_url: str,
) -> tuple[str, str, str]:
    """Return ``(title, canonical_url, readable_text)`` from HTML."""
    doc = _parse_html_document(html_text)
    canonical_url = final_url
    if doc.canonical_href:
        candidate = urllib.parse.urljoin(final_url, doc.canonical_href)
        if urllib.parse.urlsplit(candidate).scheme.lower() in ("http", "https"):
            canonical_url = candidate

    blocks = list(doc.blocks)
    if not blocks and doc.all_text_parts:
        fallback = " ".join(" ".join(doc.all_text_parts).split())
        if fallback:
            blocks.append(fallback)
    return doc.title, canonical_url, "\n\n".join(blocks)


def read_webpage_service(
    url: str,
    *,
    offset: int = 0,
    max_chars: int = DEFAULT_PAGE_CHAR_LIMIT,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Fetch and extract readable text from a public URL with SSRF protection and pagination."""
    if offset < 0:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "offset must be >= 0.")
    if max_chars < 64 or max_chars > 20_000:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "max_chars must be between 64 and 20000.",
        )

    active_fetcher = fetcher or fetch_safe_url
    resp = active_fetcher(url)
    if "html" in resp.content_type:
        title, canonical_url, full_text = parse_readable_html(resp.text, resp.final_url)
    else:
        title = resp.final_url
        canonical_url = resp.final_url
        full_text = resp.text.strip()

    total_chars = len(full_text)
    sliced = full_text[offset : offset + max_chars]
    end_offset = offset + len(sliced)
    truncated = end_offset < total_chars
    next_offset = end_offset if truncated else None

    warnings: list[str] = []
    status = ResultStatus.SUCCESS
    if truncated:
        status = ResultStatus.PARTIAL
        warnings.append(
            f"Webpage content truncated at {end_offset}/{total_chars} chars. "
            f"Pass offset={next_offset} to read the next segment."
        )

    res = ToolResult.ok(
        tool="read_webpage",
        summary=f"Read {len(sliced)} chars from '{title or canonical_url}'.",
        data={
            "requested_url": resp.requested_url,
            "final_url": resp.final_url,
            "canonical_url": canonical_url,
            "title": title,
            "content": sliced,
            "start_offset": offset,
            "end_offset": end_offset,
            "total_chars": total_chars,
            "truncated": truncated,
            "next_offset": next_offset,
        },
        warnings=warnings,
        provenance=[
            Provenance(
                provider="web_fetch",
                retrieved_at=resp.retrieved_at,
                source_url=canonical_url,
                freshness_note=f"HTTP {resp.status_code}; range [{offset}:{end_offset}].",
                untrusted_content=True,
            )
        ],
        status=status,
    )
    res.metadata.truncated = truncated
    res.metadata.next_offset = next_offset
    return res


def extract_links_service(
    url: str,
    *,
    same_domain_only: bool = False,
    max_links: int = 50,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Extract normalized HTTP(S) links from a public webpage."""
    capped = max(1, min(max_links, MAX_EXTRACTED_LINKS))
    active_fetcher = fetcher or fetch_safe_url
    resp = active_fetcher(url)
    doc = _parse_html_document(resp.text)

    base_host = (urllib.parse.urlsplit(resp.final_url).hostname or "").lower()
    seen: set[str] = set()
    links: list[dict[str, str]] = []
    total_found = 0

    for raw_href_item, raw_anchor in doc.links:
        raw_href = raw_href_item.strip()
        if not raw_href or raw_href.startswith(("#", "javascript:", "mailto:", "tel:")):
            continue
        abs_url = urllib.parse.urljoin(resp.final_url, raw_href)
        parsed = urllib.parse.urlsplit(abs_url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            continue
        if same_domain_only and parsed.hostname.lower() != base_host:
            continue
        clean_url = urllib.parse.urlunsplit(
            (parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, "")
        )
        if clean_url in seen:
            continue
        seen.add(clean_url)
        total_found += 1
        if len(links) < capped:
            anchor_text = raw_anchor[:200]
            links.append({"url": clean_url, "text": anchor_text})

    truncated = total_found > capped
    warnings = (
        [f"Returned {capped} of {total_found} unique links found on page."]
        if truncated
        else []
    )
    return ToolResult.ok(
        tool="extract_links",
        summary=f"Extracted {len(links)} links from '{resp.final_url}'.",
        data={
            "source_url": resp.final_url,
            "links": links,
            "total_unique_links": total_found,
            "truncated": truncated,
        },
        warnings=warnings,
        provenance=[
            Provenance(
                provider="web_fetch",
                retrieved_at=resp.retrieved_at,
                source_url=resp.final_url,
                untrusted_content=True,
            )
        ],
        status=ResultStatus.PARTIAL if truncated else ResultStatus.SUCCESS,
    )


def extract_tables_service(
    url: str,
    *,
    max_tables: int = 5,
    max_rows_per_table: int = 25,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Extract HTML tables (`<table>`) from a public webpage into structured rows."""
    capped_tables = max(1, min(max_tables, MAX_EXTRACTED_TABLES))
    capped_rows = max(1, min(max_rows_per_table, MAX_TABLE_ROWS))
    active_fetcher = fetcher or fetch_safe_url
    resp = active_fetcher(url)
    doc = _parse_html_document(resp.text)

    all_tables = doc.tables
    extracted: list[dict[str, Any]] = []
    truncated = len(all_tables) > capped_tables

    for idx, (raw_caption, tr_rows) in enumerate(all_tables[:capped_tables]):
        if len(tr_rows) > capped_rows:
            truncated = True
        rows_out = list(tr_rows[:capped_rows])
        caption = raw_caption or f"Table {idx + 1}"
        extracted.append(
            {
                "table_index": idx,
                "caption": caption,
                "row_count": len(rows_out),
                "rows": rows_out,
            }
        )

    warnings = (
        ["Table count or row count exceeded limit; returned bounded table preview."]
        if truncated
        else []
    )
    return ToolResult.ok(
        tool="extract_tables",
        summary=f"Extracted {len(extracted)} table(s) from '{resp.final_url}'.",
        data={
            "source_url": resp.final_url,
            "tables": extracted,
            "total_tables_found": len(all_tables),
            "truncated": truncated,
        },
        warnings=warnings,
        provenance=[
            Provenance(
                provider="web_fetch",
                retrieved_at=resp.retrieved_at,
                source_url=resp.final_url,
                untrusted_content=True,
            )
        ],
        status=ResultStatus.PARTIAL if truncated else ResultStatus.SUCCESS,
    )


def crawl_website_service(
    start_url: str,
    *,
    max_pages: int = 3,
    max_depth: int = 1,
    max_chars_per_page: int = 2000,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Bounded same-domain breadth-first crawler with per-hop SSRF validation."""
    capped_pages = max(1, min(max_pages, MAX_CRAWL_PAGES))
    capped_depth = max(0, min(max_depth, MAX_CRAWL_DEPTH))
    capped_chars = max(200, min(max_chars_per_page, DEFAULT_PAGE_CHAR_LIMIT))

    active_fetcher = fetcher or fetch_safe_url

    start_parsed = urllib.parse.urlsplit(start_url.strip())
    allowed_domain = (start_parsed.hostname or "").lower()
    if not allowed_domain:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "start_url is missing a valid hostname.")

    queue: deque[tuple[str, int]] = deque([(start_url.strip(), 0)])
    visited: set[str] = set()
    pages: list[dict[str, Any]] = []
    provenance: list[Provenance] = []
    warnings: list[str] = []
    deadline = time.monotonic() + MAX_CRAWL_DURATION_SECONDS

    while queue and len(pages) < capped_pages:
        if time.monotonic() > deadline:
            warnings.append("Crawl stopped early due to total duration budget.")
            break

        current_url, depth = queue.popleft()
        if current_url in visited:
            continue
        visited.add(current_url)

        try:
            resp = active_fetcher(current_url)
        except UltimateMCPError as exc:
            warnings.append(f"Skipped '{current_url}': [{exc.code.value}] {exc.message}")
            continue

        final_host = (urllib.parse.urlsplit(resp.final_url).hostname or "").lower()
        if final_host != allowed_domain:
            warnings.append(
                f"Skipped '{resp.final_url}' because redirect left domain '{allowed_domain}'."
            )
            continue

        title, canonical_url, text = parse_readable_html(resp.text, resp.final_url)
        excerpt = text[:capped_chars]
        pages.append(
            {
                "url": canonical_url,
                "depth": depth,
                "title": title,
                "excerpt": excerpt,
                "total_chars": len(text),
                "truncated": len(text) > capped_chars,
            }
        )
        provenance.append(
            Provenance(
                provider="web_crawler",
                retrieved_at=resp.retrieved_at,
                source_url=canonical_url,
                freshness_note=f"depth={depth}",
                untrusted_content=True,
            )
        )

        if depth < capped_depth and len(pages) < capped_pages:
            doc_links = _parse_html_document(resp.text).links
            for raw_href, _ in doc_links:
                nxt = urllib.parse.urljoin(resp.final_url, raw_href.strip())
                p_nxt = urllib.parse.urlsplit(nxt)
                if (
                    p_nxt.scheme.lower() in ("http", "https")
                    and (p_nxt.hostname or "").lower() == allowed_domain
                ):
                    clean_nxt = urllib.parse.urlunsplit(
                        (p_nxt.scheme.lower(), p_nxt.netloc, p_nxt.path or "/", p_nxt.query, "")
                    )
                    if clean_nxt not in visited and len(queue) < 25:
                        queue.append((clean_nxt, depth + 1))

    if not pages:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            f"Could not crawl any pages from '{start_url}'.",
            details={"warnings": warnings},
        )

    status = ResultStatus.PARTIAL if warnings else ResultStatus.SUCCESS
    return ToolResult.ok(
        tool="crawl_website",
        summary=f"Crawled {len(pages)} page(s) within domain '{allowed_domain}'.",
        data={
            "domain": allowed_domain,
            "pages_crawled": len(pages),
            "pages": pages,
        },
        warnings=warnings,
        provenance=provenance,
        status=status,
    )


DOCTYPE_OR_ENTITY_RE = re.compile(r"<!\s*(DOCTYPE|ENTITY)\b", re.IGNORECASE)


def _local_tag_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower() if "}" in tag else tag.lower()


def parse_rss_or_atom_xml(
    xml_text: str,
    *,
    query: str | None = None,
    max_items: int = 10,
) -> tuple[str, list[RSSFeedItem]]:
    """Safely parse RSS 2.0 or Atom XML after rejecting DOCTYPE/ENTITY declarations."""
    if DOCTYPE_OR_ENTITY_RE.search(xml_text):
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "XML feed contains forbidden DOCTYPE or ENTITY declarations.",
        )
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError as exc:
        raise UltimateMCPError(
            ErrorCode.UPSTREAM_INVALID_RESPONSE,
            f"Invalid RSS/Atom XML: {exc}",
        ) from exc

    root_name = _local_tag_name(root.tag)
    feed_title = ""
    raw_items: list[RSSFeedItem] = []

    if root_name == "rss" or root_name == "rdf":
        channel = next((c for c in root if _local_tag_name(c.tag) == "channel"), root)
        for child in channel:
            cname = _local_tag_name(child.tag)
            if cname == "title" and child.text:
                feed_title = child.text.strip()
            elif cname == "item":
                title = ""
                link = ""
                desc = ""
                pub = None
                guid = None
                for field_el in child:
                    fname = _local_tag_name(field_el.tag)
                    val = (field_el.text or "").strip()
                    if fname == "title":
                        title = val
                    elif fname == "link" and val:
                        link = val
                    elif fname in ("description", "encoded") and not desc:
                        desc = " ".join(val.split())[:600]
                    elif fname in ("pubdate", "date"):
                        pub = val
                    elif fname == "guid":
                        guid = val
                if title or link:
                    raw_items.append(
                        RSSFeedItem(
                            title=title or link,
                            link=link,
                            summary=desc,
                            published_at=pub,
                            guid=guid,
                        )
                    )
    elif root_name == "feed":
        for child in root:
            cname = _local_tag_name(child.tag)
            if cname == "title" and child.text:
                feed_title = child.text.strip()
            elif cname == "entry":
                title = ""
                link = ""
                summary = ""
                pub = None
                guid = None
                for field_el in child:
                    fname = _local_tag_name(field_el.tag)
                    val = (field_el.text or "").strip()
                    if fname == "title":
                        title = val
                    elif fname == "link":
                        href = str(field_el.attrib.get("href") or val).strip()
                        if href and not link:
                            link = href
                    elif fname in ("summary", "content") and not summary:
                        summary = " ".join(val.split())[:600]
                    elif fname in ("published", "updated") and not pub:
                        pub = val
                    elif fname == "id":
                        guid = val
                if title or link:
                    raw_items.append(
                        RSSFeedItem(
                            title=title or link,
                            link=link,
                            summary=summary,
                            published_at=pub,
                            guid=guid,
                        )
                    )
    else:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unsupported XML feed root element '<{root_name}>'.",
        )

    if query and query.strip():
        needle = query.strip().lower()
        raw_items = [
            it
            for it in raw_items
            if needle in it.title.lower() or needle in it.summary.lower()
        ]

    capped = max(1, min(max_items, 25))
    return feed_title, raw_items[:capped]


def rss_search_service(
    feed_url: str,
    *,
    query: str | None = None,
    max_items: int = 10,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Fetch and parse an RSS or Atom feed with SSRF protection and optional keyword filter."""
    active_fetcher = fetcher or fetch_safe_url
    resp = active_fetcher(feed_url)
    feed_title, items = parse_rss_or_atom_xml(resp.text, query=query, max_items=max_items)
    serialized = [
        {
            "title": it.title,
            "link": it.link,
            "summary": it.summary,
            "published_at": it.published_at,
            "guid": it.guid,
        }
        for it in items
    ]
    return ToolResult.ok(
        tool="rss_search",
        summary=f"Retrieved {len(serialized)} item(s) from feed '{feed_title or resp.final_url}'.",
        data={
            "feed_url": resp.final_url,
            "feed_title": feed_title,
            "query": query,
            "items": serialized,
        },
        provenance=[
            Provenance(
                provider="rss_atom",
                retrieved_at=resp.retrieved_at,
                source_url=resp.final_url,
                untrusted_content=True,
            )
        ],
    )


def legacy_read_article_service(
    url: str,
    *,
    fetcher: PageFetcher | None = None,
) -> ToolResult:
    """Compatibility wrapper for legacy `read_article` enforcing v2 SSRF and size policies."""
    res = read_webpage_service(url, offset=0, max_chars=8_000, fetcher=fetcher)
    res.warnings.append("Deprecated tool 'read_article': prefer 'read_webpage'.")
    return res

