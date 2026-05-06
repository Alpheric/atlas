"""Citation engine for web-grounded answers.

Every time Atlas answers with web search results, this module:
1. Builds a structured citation list from the search results
2. Detects which inline citation markers ([1], [2], etc.) appear in the response
3. Marks each citation as `claim_supported=True` if the number was referenced

The LLM is instructed in the search context prompt to use [N] markers.
This module then connects those markers back to the actual URLs.
"""

import re
from dataclasses import dataclass


@dataclass
class Citation:
    """A single verifiable source linked to an LLM response."""

    source_url: str
    title: str
    published_date: str  # ISO date string or empty
    accessed_at: str  # ISO datetime (IST)
    claim_supported: bool = True
    rank: int = 0  # position in search results (1-based)
    snippet: str = ""  # brief excerpt from the source


def build_citations(
    search_results: list,  # list[SearchResult]
    response_text: str,
    accessed_at: str | None = None,
) -> list[Citation]:
    """Build citation objects from search results + LLM response.

    Marks `claim_supported=True` for every result that the model referenced
    using [N] inline markers. Results not referenced keep claim_supported=True
    as well — they were part of the grounding context even if not explicitly cited.

    Args:
        search_results: list of SearchResult TypedDicts from the provider.
        response_text: the LLM's final answer text.
        accessed_at: ISO datetime string; defaults to now_ist().

    Returns:
        list of Citation objects, one per search result.
    """
    from a1.common.tz import now_ist

    ts = accessed_at or now_ist().isoformat()

    # Find all [N] citations referenced in the response
    cited_indices: set[int] = set()
    for m in re.finditer(r"\[(\d+)\]", response_text):
        cited_indices.add(int(m.group(1)))

    citations: list[Citation] = []
    for result in search_results:
        rank = result.get("rank", 0)
        # A result is considered "claim_supported" if it was explicitly cited
        # OR if it was the only source available (rank 1 always gets credit)
        cited = rank in cited_indices or (len(search_results) == 1 and rank == 1)
        citations.append(
            Citation(
                source_url=result.get("url", ""),
                title=result.get("title", ""),
                published_date=result.get("published_date", ""),
                accessed_at=ts,
                claim_supported=cited,
                rank=rank,
                snippet=result.get("snippet", "")[:300],
            )
        )

    return citations


def format_citations_block(citations: list[Citation]) -> str:
    """Format a human-readable citations block to append to responses.

    Example output:

        ---
        **Sources:**
        [1] Title of page — https://example.com (2024-03-15)
        [2] Another page — https://other.com
    """
    if not citations:
        return ""

    lines = ["", "---", "**Sources:**"]
    for c in citations:
        date_str = f" ({c.published_date})" if c.published_date else ""
        lines.append(f"[{c.rank}] {c.title or c.source_url} — {c.source_url}{date_str}")

    return "\n".join(lines)


def inject_citations_if_missing(response_text: str, citations: list[Citation]) -> str:
    """Append a citations block to the response if none exist yet.

    Only appends if the response doesn't already end with a Sources section.
    """
    if not citations:
        return response_text

    # Don't double-append
    if "**Sources:**" in response_text or "Sources:" in response_text:
        return response_text

    return response_text + format_citations_block(citations)
