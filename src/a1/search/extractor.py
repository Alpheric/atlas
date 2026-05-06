"""Lightweight page content extractor.

For the top N search results, fetches the raw HTML and extracts clean text.
Gracefully handles timeouts, errors, and bot-blocked pages.

Design goals:
- Non-blocking: all fetches run concurrently with a wall-clock timeout
- Lightweight: no headless browser; pure HTTP + regex/stdlib HTML parsing
- Safe: respects robots.txt signal via 403/429 status codes
- Short: returns only a 500-word summary, not the full page
"""

import asyncio
import html
import re
import unicodedata

import httpx

from a1.common.logging import get_logger
from a1.search.providers.base import SearchResult

log = get_logger("search.extractor")

# Maximum characters to keep per page (roughly 500-700 words)
_MAX_CONTENT_CHARS = 3000
# Per-page fetch timeout (seconds)
_FETCH_TIMEOUT = 6.0
# Concurrent fetch limit
_MAX_CONCURRENT = 3

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (compatible; AtlasBot/1.0; +https://alpheric.com)"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

# Block-listed domains (paywalled / anti-scrape) — skip extraction entirely
_BLOCKED_DOMAINS = frozenset(
    {
        "nytimes.com",
        "wsj.com",
        "ft.com",
        "bloomberg.com",
        "economist.com",
        "reuters.com",  # has a lenient API; full pages are JS-rendered
        "linkedin.com",
        "facebook.com",
        "instagram.com",
        "twitter.com",
        "x.com",
        "tiktok.com",
    }
)


class ExtractedPage:
    """Cleaned page content from a search result URL."""

    __slots__ = ("url", "title", "content", "word_count", "source_date", "ok")

    def __init__(
        self,
        url: str,
        title: str = "",
        content: str = "",
        word_count: int = 0,
        source_date: str = "",
        ok: bool = True,
    ):
        self.url = url
        self.title = title
        self.content = content
        self.word_count = word_count
        self.source_date = source_date
        self.ok = ok

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "word_count": self.word_count,
            "source_date": self.source_date,
            "ok": self.ok,
        }


async def extract_pages(
    results: list[SearchResult],
    max_pages: int = 3,
) -> list[ExtractedPage]:
    """Fetch and clean the top `max_pages` results concurrently.

    Returns one ExtractedPage per result (may have ok=False on failure).
    Never raises — errors are absorbed and logged.
    """
    targets = results[:max_pages]
    if not targets:
        return []

    sem = asyncio.Semaphore(_MAX_CONCURRENT)

    async def _fetch_one(result: SearchResult) -> ExtractedPage:
        import urllib.parse
        domain = urllib.parse.urlparse(result["url"]).netloc.lstrip("www.")
        # Skip extraction for known blocked/JS-heavy domains
        if any(domain.endswith(b) for b in _BLOCKED_DOMAINS):
            return ExtractedPage(url=result["url"], ok=False)
        async with sem:
            return await _fetch_and_extract(result["url"])

    pages = await asyncio.gather(
        *[_fetch_one(r) for r in targets],
        return_exceptions=True,
    )

    extracted: list[ExtractedPage] = []
    for i, p in enumerate(pages):
        if isinstance(p, Exception):
            log.debug(f"Extraction exception for {targets[i]['url']}: {p}")
            extracted.append(ExtractedPage(url=targets[i]["url"], ok=False))
        else:
            extracted.append(p)

    return extracted


async def _fetch_and_extract(url: str) -> ExtractedPage:
    """Fetch one URL and return cleaned text."""
    async with httpx.AsyncClient(
        headers=_HEADERS,
        timeout=httpx.Timeout(_FETCH_TIMEOUT),
        follow_redirects=True,
        verify=False,  # some enterprise/news sites have cert issues
    ) as client:
        try:
            resp = await client.get(url)
            # Treat 4xx/5xx as extraction failure (don't raise for 403/429)
            if resp.status_code >= 400:
                return ExtractedPage(url=url, ok=False)

            content_type = resp.headers.get("content-type", "")
            if "html" not in content_type:
                return ExtractedPage(url=url, ok=False)

            raw_html = resp.text
        except (httpx.TimeoutException, httpx.NetworkError, httpx.TooManyRedirects) as e:
            log.debug(f"Fetch failed {url}: {type(e).__name__}")
            return ExtractedPage(url=url, ok=False)

    title, text, date = _parse_html(raw_html)
    text = text[:_MAX_CONTENT_CHARS]
    word_count = len(text.split())
    return ExtractedPage(
        url=url,
        title=title,
        content=text,
        word_count=word_count,
        source_date=date,
        ok=True,
    )


def _parse_html(raw: str) -> tuple[str, str, str]:
    """Extract (title, main_text, date_hint) from raw HTML using stdlib only."""
    # Decode HTML entities
    raw = html.unescape(raw)

    # Title
    title = ""
    m = re.search(r"<title[^>]*>(.*?)</title>", raw, re.IGNORECASE | re.DOTALL)
    if m:
        title = _clean_text(m.group(1))

    # Date hint from common meta tags
    date = ""
    for pattern in [
        r'<meta[^>]+(?:property|name)=["\'](?:article:published_time|date|pubdate|og:updated_time)["\'][^>]+content=["\']([\d\-T:Z+]+)',
        r'<time[^>]+datetime=["\']([\d\-T:Z+]+)',
    ]:
        dm = re.search(pattern, raw, re.IGNORECASE)
        if dm:
            date = dm.group(1)[:10]
            break

    # Remove non-content elements
    raw = re.sub(r"<(script|style|nav|header|footer|aside|form|noscript)[^>]*>.*?</\1>",
                 "", raw, flags=re.DOTALL | re.IGNORECASE)
    # Remove all remaining tags
    raw = re.sub(r"<[^>]+>", " ", raw)
    text = _clean_text(raw)

    # Deduplicate blank lines
    text = re.sub(r"\n{3,}", "\n\n", text)
    return title, text, date


def _clean_text(text: str) -> str:
    """Normalize whitespace and remove control characters."""
    # Normalize unicode
    text = unicodedata.normalize("NFKD", text)
    # Replace various whitespace with single space
    text = re.sub(r"[ \t\r]+", " ", text)
    text = re.sub(r"\n +", "\n", text)
    text = text.strip()
    return text
