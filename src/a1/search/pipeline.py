"""Web Search Pipeline — the main orchestrator.

Sits between intent detection and the LLM call. When search is needed:

1. Apply PII masking to the query (same engine as LLM path)
2. Block clearly unsafe queries (internal secrets, PII-heavy)
3. Search via the active provider (Tavily / Exa / Brave)
4. Fetch + clean page content for the top N results (async, timeout-guarded)
5. Build a grounding context block injected as a system message
6. Persist run + results to DB in the background
7. Return SearchContext to the pipeline

The LLM receives the search results and is instructed to answer only from them
and to include inline [N] citations. The citation engine then links those
markers to stored source records.
"""

import asyncio
import hashlib
import re as _re
import time
import uuid
from dataclasses import dataclass, field

from a1.common.logging import get_logger
from a1.common.tz import now_ist
from a1.search.citation import Citation, build_citations
from a1.search.extractor import ExtractedPage, extract_pages
from a1.search.intent import extract_search_query, needs_web_search
from a1.search.providers.base import SearchResult
from a1.search.providers.registry import search_registry
from config.settings import settings

log = get_logger("search.pipeline")

# ---------------------------------------------------------------------------
# Sensitive query guard — block before sending to external search
# ---------------------------------------------------------------------------

_BLOCK_PATTERNS = [
    r"(?i)\b(sk-[a-z0-9]{32,}|AKIA[A-Z0-9]{16})\b",  # API/AWS keys
    r"(?i)\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b",  # SSN
    r"(?i)\b(?:\d{4}[- ]?){3}\d{4}\b",  # credit card
    r"(?i)\b[a-z0-9._%+\-]+@[a-z0-9.\-]+\.[a-z]{2,}\b",  # email
    r"(?i)\b(password|passwd|secret|private_key)\s*[=:]\s*\S+",  # key=value
]

_COMPILED_BLOCKS = [_re.compile(p) for p in _BLOCK_PATTERNS]


def _is_unsafe_query(query: str) -> tuple[bool, str]:
    """Return (is_unsafe, reason). Blocks queries that leak PII or secrets."""
    for pat in _COMPILED_BLOCKS:
        if pat.search(query):
            return True, f"sensitive_pattern: {pat.pattern[:40]}"
    return False, ""


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass
class SearchContext:
    """Everything the pipeline needs to inject search results into a prompt."""

    run_id: str = ""
    query: str = ""
    provider: str = ""
    results: list[SearchResult] = field(default_factory=list)
    pages: list[ExtractedPage] = field(default_factory=list)
    citations: list[Citation] = field(default_factory=list)
    context_block: str = ""  # the formatted text injected into the prompt
    latency_ms: int = 0
    blocked: bool = False
    block_reason: str = ""
    search_reason: str = ""  # why we searched ("high_intent", etc.)
    intent_score: int = 0


# ---------------------------------------------------------------------------
# Main pipeline entry point
# ---------------------------------------------------------------------------


def _vertex_search_available() -> bool:
    """True when Vertex is healthy and web search grounding is enabled."""
    try:
        from a1.providers.registry import provider_registry

        return settings.vertex_web_search_enabled and provider_registry.is_healthy("vertex")
    except Exception:
        return False


async def maybe_search(
    messages: list,
    task_type: str | None = None,
    workspace_id: str | None = None,
    session_id: str | None = None,
    atlas_model: str | None = None,
) -> SearchContext | None:
    """Decide whether to search and, if so, run the full search pipeline.

    Priority:
      1. Vertex AI Google Search grounding (when A1_VERTEX_WEB_SEARCH_ENABLED=true)
         — returns a SearchContext with provider="vertex_grounding" and no
           separate HTTP round-trip (grounding is done inside the LLM call)
      2. External search provider (Tavily / Exa / Brave)

    Returns a SearchContext if search was performed, None if skipped.
    Never raises — all errors are absorbed so the LLM path is unaffected.
    """
    if not settings.web_search_enabled:
        return None

    # Extract the user query
    query = extract_search_query(messages)
    if not query:
        return None

    # Intent check
    threshold = settings.web_search_intent_threshold
    should_search, score, reason = needs_web_search(query, threshold=threshold)
    if not should_search:
        log.debug(f"[search] Intent score {score} < {threshold} — skipping. Query: {query[:60]!r}")
        return None

    # Security gate — PII-mask query before sending anywhere external
    masked_query = _mask_query(query)
    unsafe, block_reason = _is_unsafe_query(masked_query)
    if unsafe:
        log.warning(f"[search] Blocked unsafe query: {block_reason}")
        _record_blocked(masked_query, block_reason, workspace_id, atlas_model)
        return None

    # ------------------------------------------------------------------
    # Path 1: Vertex AI Google Search grounding
    # When enabled, signal the CorePipeline to route this request through
    # a Vertex/Gemini model with googleSearch tool enabled. No separate
    # HTTP call needed — grounding happens inside the LLM call itself.
    # ------------------------------------------------------------------
    if _vertex_search_available():
        log.info(
            f"[search] Intent={score} reason={reason} provider=vertex_grounding "
            f"query={masked_query[:60]!r}"
        )
        ctx = SearchContext(
            run_id=str(uuid.uuid4()),
            query=masked_query,
            provider="vertex_grounding",
            search_reason=reason,
            intent_score=score,
            # No results/pages/citations yet — grounding metadata comes back
            # from VertexProvider.complete() after the LLM call.
            context_block="",  # no injected block needed; grounding is inline
        )
        # Record metric
        try:
            from a1.common.metrics import metrics

            metrics.record_search(provider="vertex_grounding", latency_ms=0, result_count=0)
        except Exception:
            pass
        return ctx

    # ------------------------------------------------------------------
    # Path 2: External search provider (Tavily / Exa / Brave)
    # ------------------------------------------------------------------
    if not search_registry.is_available():
        log.debug("[search] No search provider available — skipping")
        return None

    log.info(
        f"[search] Intent={score} reason={reason} provider={search_registry.active_provider.name} "  # type: ignore[union-attr]
        f"query={masked_query[:60]!r}"
    )

    t0 = time.time()
    try:
        results, provider_name = await asyncio.wait_for(
            search_registry.search(masked_query, max_results=settings.web_search_max_results),
            timeout=settings.web_search_timeout_s,
        )
    except asyncio.TimeoutError:
        log.warning(f"[search] Search timed out after {settings.web_search_timeout_s}s")
        return None
    except RuntimeError as e:
        log.warning(f"[search] All providers failed: {e}")
        return None

    latency_ms = int((time.time() - t0) * 1000)

    # Content extraction for top results
    pages: list[ExtractedPage] = []
    if settings.web_search_extract_pages and results:
        try:
            pages = await asyncio.wait_for(
                extract_pages(results, max_pages=settings.web_search_extract_max),
                timeout=settings.web_search_extract_timeout_s,
            )
        except asyncio.TimeoutError:
            log.debug("[search] Page extraction timed out — using snippets only")
        except Exception as e:
            log.debug(f"[search] Page extraction failed: {e}")

    # Build grounding context block
    context_block = _build_context_block(results, pages, masked_query)

    # Build citation stubs (will be updated after LLM responds)
    citations = build_citations(results, response_text="", accessed_at=now_ist().isoformat())

    run_id = str(uuid.uuid4())
    ctx = SearchContext(
        run_id=run_id,
        query=masked_query,
        provider=provider_name,
        results=results,
        pages=pages,
        citations=citations,
        context_block=context_block,
        latency_ms=latency_ms,
        search_reason=reason,
        intent_score=score,
    )

    # Persist to DB in the background — never block the LLM call
    asyncio.create_task(
        _persist_search_run(ctx, workspace_id=workspace_id, atlas_model=atlas_model)
    )

    # Record in-memory metrics
    from a1.common.metrics import metrics

    metrics.record_search(provider=provider_name, latency_ms=latency_ms, result_count=len(results))

    return ctx


def inject_search_context(messages: list, ctx: SearchContext) -> list:
    """Inject the search context block into the message list.

    Appends / replaces the system message so the LLM sees:
    - Atlas identity + original system prompt
    - Web Search Results section with source list

    Returns a new list (does not mutate the input).
    """
    from a1.proxy.request_models import MessageInput

    context_block = ctx.context_block
    if not context_block:
        return messages

    new_messages = []
    system_injected = False

    for msg in messages:
        role = getattr(msg, "role", "") if not isinstance(msg, dict) else msg.get("role", "")
        if role == "system":
            # Append search context to existing system message
            existing = (
                getattr(msg, "content", "") if not isinstance(msg, dict) else msg.get("content", "")
            )
            combined = (existing or "").rstrip() + "\n\n" + context_block
            new_msg = MessageInput(role="system", content=combined)
            new_messages.append(new_msg)
            system_injected = True
        else:
            new_messages.append(msg)

    if not system_injected:
        # No existing system message — prepend one
        new_messages.insert(0, MessageInput(role="system", content=context_block))

    return new_messages


# ---------------------------------------------------------------------------
# Context block formatter
# ---------------------------------------------------------------------------


def _build_context_block(
    results: list[SearchResult],
    pages: list[ExtractedPage],
    query: str,
) -> str:
    """Format search results into a prompt-ready context block.

    Instructs the model to:
    - Answer only from provided sources
    - Include inline citations as [N]
    - Not invent information beyond what the sources say
    """
    if not results:
        return ""

    # Build page content lookup for richer snippets
    page_content: dict[str, str] = {}
    for page in pages:
        if page.ok and page.content:
            page_content[page.url] = page.content[:1500]

    lines = [
        "## Web Search Results",
        f"The following sources were retrieved for: *{query}*",
        "",
        "**Instructions:**",
        "- Answer ONLY from the sources below.",
        "- Include inline citations using [N] markers (e.g. 'Python 3.12 [1]').",
        "- If the sources don't contain enough information, say so clearly.",
        "- Do NOT fabricate facts not present in the sources.",
        "",
    ]

    for r in results:
        n = r["rank"]
        title = r["title"] or r["url"]
        url = r["url"]
        date = r["published_date"]
        snippet = r["snippet"] or ""

        # Prefer extracted page content over API snippet if available
        full_content = page_content.get(url, "")
        body = full_content if full_content else snippet

        header = f"[{n}] **{title}**"
        if date:
            header += f" ({date})"
        header += f"\nURL: {url}"
        lines.append(header)
        if body:
            # Truncate to ~400 chars per source to keep total prompt manageable
            lines.append(body[:400].strip())
        lines.append("")

    lines.append("---")
    lines.append("Based on the sources above, please provide a comprehensive, accurate answer.")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Security helpers
# ---------------------------------------------------------------------------


def _mask_query(query: str) -> str:
    """Apply PII masking to the search query before sending to external APIs."""
    try:
        from a1.security.pii_masker import pii_masker

        result = pii_masker.mask(query)
        # mask() returns a MaskResult object with .masked_text attribute
        return result.masked_text if hasattr(result, "masked_text") else str(result)
    except Exception:
        return query  # never block on masking failure


def _record_blocked(
    query: str,
    reason: str,
    workspace_id: str | None,
    atlas_model: str | None,
) -> None:
    """Fire-and-forget: record a blocked search attempt."""
    asyncio.create_task(_persist_blocked_run(query, reason, workspace_id, atlas_model))


# ---------------------------------------------------------------------------
# DB persistence (background tasks)
# ---------------------------------------------------------------------------


async def _persist_search_run(
    ctx: SearchContext,
    workspace_id: str | None = None,
    atlas_model: str | None = None,
) -> None:
    """Persist search run + results + page extractions to the database."""
    try:
        from a1.db.engine import async_session
        from a1.db.models import WebExtractedPage, WebSearchResult, WebSearchRun

        async with async_session() as session:
            # Hash query for deduplication analytics (not stored in plain text)
            query_hash = hashlib.sha256(ctx.query.encode()).hexdigest()[:16]

            run = WebSearchRun(
                id=uuid.UUID(ctx.run_id),
                workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
                query_masked=ctx.query,
                query_raw_hash=query_hash,
                provider=ctx.provider,
                result_count=len(ctx.results),
                latency_ms=ctx.latency_ms,
                blocked=ctx.blocked,
                block_reason=ctx.block_reason or None,
                search_reason=ctx.search_reason,
                atlas_model=atlas_model,
            )
            session.add(run)
            await session.flush()

            result_id_map: dict[int, uuid.UUID] = {}
            for r in ctx.results:
                sr = WebSearchResult(
                    run_id=run.id,
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    snippet=r.get("snippet", "")[:1000],
                    published_date=r.get("published_date", "") or None,
                    source=r.get("source", ""),
                    rank=r.get("rank", 0),
                    was_extracted=False,
                )
                session.add(sr)
                await session.flush()
                result_id_map[r.get("rank", 0)] = sr.id

            for page in ctx.pages:
                if not page.ok:
                    continue
                # Find matching result by URL
                rank = next((r["rank"] for r in ctx.results if r["url"] == page.url), None)
                if rank is None:
                    continue
                result_id = result_id_map.get(rank)
                if result_id is None:
                    continue

                ep = WebExtractedPage(
                    result_id=result_id,
                    url=page.url,
                    content_summary=page.content[:2000] if page.content else None,
                    word_count=page.word_count,
                    source_date=page.source_date or None,
                    extraction_ok=page.ok,
                )
                session.add(ep)

            await session.commit()
            log.debug(f"[search] Persisted run {ctx.run_id}: {len(ctx.results)} results")

    except Exception as e:
        log.warning(f"[search] Failed to persist search run: {e}")


async def _persist_blocked_run(
    query: str,
    reason: str,
    workspace_id: str | None,
    atlas_model: str | None,
) -> None:
    """Persist a blocked search run record."""
    try:
        from a1.db.engine import async_session
        from a1.db.models import WebSearchRun

        async with async_session() as session:
            run = WebSearchRun(
                workspace_id=uuid.UUID(workspace_id) if workspace_id else None,
                query_masked="[BLOCKED]",
                query_raw_hash=hashlib.sha256(query.encode()).hexdigest()[:16],
                provider="blocked",
                result_count=0,
                latency_ms=0,
                blocked=True,
                block_reason=reason,
                search_reason="security_block",
                atlas_model=atlas_model,
            )
            session.add(run)
            await session.commit()
    except Exception as e:
        log.debug(f"[search] Failed to persist blocked run: {e}")


async def persist_citations(
    run_id: str,
    routing_decision_id: str | None,
    citations: list[Citation],
) -> None:
    """Store citation records after the LLM has responded."""
    if not citations:
        return
    try:
        from a1.db.engine import async_session
        from a1.db.models import WebCitation

        async with async_session() as session:
            for c in citations:
                session.add(
                    WebCitation(
                        run_id=uuid.UUID(run_id),
                        routing_decision_id=(
                            uuid.UUID(routing_decision_id) if routing_decision_id else None
                        ),
                        source_url=c.source_url,
                        title=c.title,
                        published_date=c.published_date or None,
                        accessed_at=now_ist(),
                        claim_supported=c.claim_supported,
                        rank=c.rank,
                    )
                )
            await session.commit()
    except Exception as e:
        log.debug(f"[search] Failed to persist citations: {e}")
