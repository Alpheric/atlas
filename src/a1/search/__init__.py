"""Web Search Tool Layer for Atlas.

Detects when a request needs live information, queries a search provider,
extracts page content, injects grounding context into the LLM prompt, and
attaches citations to every web-grounded answer.

Providers: Tavily (default), Exa, Brave (plug-in via SearchProviderRegistry).
"""
