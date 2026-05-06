"""Tavily search provider adapter.

Tavily is an AI-optimised search API designed for RAG pipelines.
Docs: https://docs.tavily.com/docs/tavily-api/rest_api

Cost: ~$0.004 per search (basic) / ~$0.006 (advanced) as of 2026.
Set TAVILY_API_KEY (or A1_TAVILY_API_KEY) in your .env file.
"""

import urllib.parse

import httpx

from a1.common.logging import get_logger

from .base import SearchProvider, SearchResult

log = get_logger("search.tavily")

_TAVILY_SEARCH_URL = "https://api.tavily.com/search"


class TavilyProvider(SearchProvider):
    """Tavily search adapter.

    Uses the /search endpoint with search_depth="basic" by default.
    Switch to search_depth="advanced" for richer snippets (higher cost).
    """

    name = "tavily"

    def __init__(self, api_key: str, search_depth: str = "basic"):
        self._api_key = api_key
        self._search_depth = search_depth
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {
            "api_key": self._api_key,
            "query": query,
            "search_depth": self._search_depth,
            "max_results": max_results,
            "include_answer": False,  # we build our own answer via the LLM
            "include_raw_content": False,
            "include_images": False,
        }
        resp = await self._client.post(_TAVILY_SEARCH_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        for i, item in enumerate(data.get("results", []), start=1):
            url = item.get("url", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=item.get("content", ""),
                    published_date=item.get("published_date", ""),
                    source=_domain(url),
                    rank=i,
                )
            )
        log.debug(f"Tavily returned {len(results)} results for: {query[:60]!r}")
        return results

    async def health_check(self) -> bool:
        try:
            resp = await self._client.post(
                _TAVILY_SEARCH_URL,
                json={
                    "api_key": self._api_key,
                    "query": "test",
                    "max_results": 1,
                    "search_depth": "basic",
                },
                timeout=8.0,
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning(f"Tavily health check failed: {e}")
            return False

    def estimate_cost(self, query_count: int) -> float:
        # Basic: ~$0.004/search; Advanced: ~$0.006/search
        rate = 0.006 if self._search_depth == "advanced" else 0.004
        return query_count * rate


def _domain(url: str) -> str:
    """Extract domain from URL, e.g. 'https://docs.python.org/3/' → 'docs.python.org'."""
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""
