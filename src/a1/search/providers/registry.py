"""Search provider registry — singleton that holds all configured search adapters.

Priority order: first healthy provider wins. Providers are tried in the order
they were registered (highest priority first).

Usage::

    from a1.search.providers.registry import search_registry
    results = await search_registry.search("Python 3.12 changes", max_results=5)
"""

import asyncio

from a1.common.logging import get_logger

from .base import SearchProvider, SearchResult

log = get_logger("search.registry")


class SearchProviderRegistry:
    """Manages multiple search provider adapters with automatic failover.

    Providers are tried in registration order. On failure the next is attempted.
    Health is lazily tracked — a provider that errored on the last call is
    skipped until a health check passes or the server restarts.
    """

    def __init__(self):
        self._providers: list[SearchProvider] = []
        self._healthy: dict[str, bool] = {}

    def register(self, provider: SearchProvider, *, primary: bool = False) -> None:
        """Add a provider. Pass primary=True to insert at the front of the list."""
        if primary:
            self._providers.insert(0, provider)
        else:
            self._providers.append(provider)
        self._healthy[provider.name] = True  # optimistic until first call
        log.info(f"Search provider registered: {provider.name}")

    @property
    def active_provider(self) -> SearchProvider | None:
        """First healthy registered provider, or None if none are available."""
        for p in self._providers:
            if self._healthy.get(p.name, True):
                return p
        return None

    def is_available(self) -> bool:
        """True if at least one provider is registered and healthy."""
        return self.active_provider is not None

    async def search(
        self,
        query: str,
        max_results: int = 5,
    ) -> tuple[list[SearchResult], str]:
        """Search with automatic failover.

        Returns (results, provider_name). Raises RuntimeError if all providers fail.
        """
        last_err: Exception | None = None
        for provider in self._providers:
            if not self._healthy.get(provider.name, True):
                continue
            try:
                results = await provider.search(query, max_results=max_results)
                self._healthy[provider.name] = True
                return results, provider.name
            except Exception as exc:
                log.warning(f"Search provider {provider.name} failed: {exc}")
                self._healthy[provider.name] = False
                last_err = exc

        raise RuntimeError(f"All search providers failed. Last error: {last_err}") from last_err

    async def health_check_all(self) -> dict[str, bool]:
        """Run health checks for all providers concurrently."""
        if not self._providers:
            return {}

        async def _check(p: SearchProvider) -> tuple[str, bool]:
            try:
                ok = await p.health_check()
                self._healthy[p.name] = ok
                return p.name, ok
            except Exception as e:
                log.warning(f"Health check error for {p.name}: {e}")
                self._healthy[p.name] = False
                return p.name, False

        results = await asyncio.gather(*[_check(p) for p in self._providers])
        return dict(results)

    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    def status(self) -> list[dict]:
        return [
            {"name": p.name, "healthy": self._healthy.get(p.name, True)} for p in self._providers
        ]

    async def aclose_all(self) -> None:
        """Close all registered providers' HTTP clients. Called at app shutdown."""
        if not self._providers:
            return
        await asyncio.gather(
            *(p.aclose() for p in self._providers),
            return_exceptions=True,
        )
        log.info(f"Closed {len(self._providers)} search provider client(s)")


# Singleton — initialised at app startup from config/settings.py
search_registry = SearchProviderRegistry()


def init_search_providers() -> None:
    """Initialise search providers from environment settings.

    Called once during app startup (app.py). Safe to call multiple times —
    providers are only registered if the corresponding API key is set.
    """
    from config.settings import settings

    registered = 0

    if settings.tavily_api_key:
        from a1.search.providers.tavily import TavilyProvider

        search_registry.register(
            TavilyProvider(
                api_key=settings.tavily_api_key,
                search_depth=settings.web_search_depth,
            ),
            primary=True,
        )
        registered += 1

    if settings.exa_api_key:
        from a1.search.providers.exa import ExaProvider

        search_registry.register(ExaProvider(api_key=settings.exa_api_key))
        registered += 1

    if settings.brave_api_key:
        from a1.search.providers.brave import BraveProvider

        search_registry.register(BraveProvider(api_key=settings.brave_api_key))
        registered += 1

    if registered == 0:
        log.info(
            "No search provider API keys configured. "
            "Set A1_TAVILY_API_KEY, A1_EXA_API_KEY, or A1_BRAVE_API_KEY to enable web search."
        )
    else:
        log.info(f"Initialised {registered} search provider(s): {search_registry.provider_names()}")
