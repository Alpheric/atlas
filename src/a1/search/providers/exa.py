"""Exa (formerly Metaphor) search provider adapter.

Exa uses neural embedding search — excellent for research and semantic queries.
Docs: https://docs.exa.ai/reference/search

Cost: ~$0.005 per search as of 2026.
Set EXA_API_KEY (or A1_EXA_API_KEY) in your .env file.
"""

import urllib.parse

import httpx

from a1.common.logging import get_logger

from .base import SearchProvider, SearchResult

log = get_logger("search.exa")

_EXA_SEARCH_URL = "https://api.exa.ai/search"


class ExaProvider(SearchProvider):
    """Exa neural search adapter."""

    name = "exa"

    def __init__(self, api_key: str, use_autoprompt: bool = True):
        self._api_key = api_key
        self._use_autoprompt = use_autoprompt
        self._client = httpx.AsyncClient(
            headers={"x-api-key": api_key, "Content-Type": "application/json"},
            timeout=httpx.Timeout(connect=5.0, read=20.0, write=10.0, pool=5.0),
        )

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        payload = {
            "query": query,
            "numResults": max_results,
            "useAutoprompt": self._use_autoprompt,
            "type": "auto",  # "neural" | "keyword" | "auto"
            "contents": {
                "text": {"maxCharacters": 500, "includeHtmlTags": False},
            },
        }
        resp = await self._client.post(_EXA_SEARCH_URL, json=payload)
        resp.raise_for_status()
        data = resp.json()

        results: list[SearchResult] = []
        for i, item in enumerate(data.get("results", []), start=1):
            url = item.get("url", "")
            text = ""
            if "text" in item:
                text_obj = item["text"]
                text = text_obj if isinstance(text_obj, str) else text_obj.get("text", "")
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    snippet=text or item.get("summary", ""),
                    published_date=item.get("publishedDate", ""),
                    source=_domain(url),
                    rank=i,
                )
            )
        log.debug(f"Exa returned {len(results)} results for: {query[:60]!r}")
        return results

    async def health_check(self) -> bool:
        try:
            resp = await self._client.post(
                _EXA_SEARCH_URL,
                json={"query": "test", "numResults": 1},
                timeout=8.0,
            )
            return resp.status_code == 200
        except Exception as e:
            log.warning(f"Exa health check failed: {e}")
            return False

    def estimate_cost(self, query_count: int) -> float:
        return query_count * 0.005


def _domain(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""
