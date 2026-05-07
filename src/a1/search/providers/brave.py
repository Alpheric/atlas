"""Brave Search provider adapter.

Privacy-first search engine with a generous free tier (2000 queries/month).
Docs: https://api.search.brave.com/app/documentation/web-search

Cost: Free tier up to 2000 queries/month; paid ~$0.003/query after.
Set BRAVE_API_KEY (or A1_BRAVE_API_KEY) in your .env file.
"""

import urllib.parse

import httpx

from a1.common.logging import get_logger

from .base import SearchProvider, SearchResult

log = get_logger("search.brave")

_BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"


class BraveProvider(SearchProvider):
    """Brave Search adapter."""

    name = "brave"

    def __init__(self, api_key: str, country: str = "us", lang: str = "en"):
        self._api_key = api_key
        self._country = country
        self._lang = lang
        self._client = httpx.AsyncClient(
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": api_key,
            },
            timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        )

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        params = {
            "q": query,
            "count": min(max_results, 20),  # Brave max is 20
            "country": self._country,
            "search_lang": self._lang,
            "text_decorations": False,
        }
        resp = await self._client.get(_BRAVE_SEARCH_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        web_results = data.get("web", {}).get("results", [])
        for i, item in enumerate(web_results[:max_results], start=1):
            url = item.get("url", "")
            # Build snippet from description + extra snippets
            desc = item.get("description", "")
            extra = " ".join(s.get("text", "") for s in item.get("extra_snippets", []))
            snippet = (desc + " " + extra).strip()
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=snippet[:600],
                    published_date=item.get("age", ""),  # Brave uses "age" not ISO date
                    source=_domain(url),
                    rank=i,
                )
            )
        log.debug(f"Brave returned {len(results)} results for: {query[:60]!r}")
        return results

    async def health_check(self) -> bool:
        try:
            resp = await self._client.get(
                _BRAVE_SEARCH_URL,
                params={"q": "test", "count": 1},
                timeout=8.0,
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning(f"Brave health check failed: {e}")
            return False

    def estimate_cost(self, query_count: int) -> float:
        return query_count * 0.003


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""
