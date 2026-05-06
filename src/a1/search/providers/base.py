"""Abstract base class for web search providers.

Each provider adapter must return results in the normalised SearchResult shape
so the rest of the pipeline (extractor, citation engine, LLM context builder)
can work provider-agnostically.
"""

from abc import ABC, abstractmethod
from typing import TypedDict


class SearchResult(TypedDict):
    """Normalised search result — every provider must return this shape."""

    title: str
    url: str
    snippet: str           # 1-3 sentence excerpt from the page
    published_date: str    # ISO 8601 or empty string
    source: str            # domain name, e.g. "docs.python.org"
    rank: int              # 1-based position in result list


class SearchProvider(ABC):
    """Base class for web search provider adapters."""

    name: str  # e.g. "tavily", "exa", "brave"

    @abstractmethod
    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Run a web search and return normalised results.

        Must raise httpx.HTTPStatusError or a subclass on HTTP errors so the
        registry can detect provider failures and fall back gracefully.
        """
        ...

    async def health_check(self) -> bool:
        """Return True if the provider is reachable and the API key is valid."""
        return True

    def estimate_cost(self, query_count: int) -> float:
        """Estimate USD cost for `query_count` searches (approximate)."""
        return 0.0
