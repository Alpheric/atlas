"""Long-context chunking — MapReduce over oversized documents.

When a request's token count would exceed a provider's context window, this
module splits the large content into overlapping chunks, runs a partial-answer
pass over each chunk in parallel, then synthesises into a single final answer.

Strategy (MapReduce):
  1. Identify the largest text block (system message or last user message).
  2. Split it into overlapping chunks that fit the provider's window.
  3. Map: ask the model to answer the user's query using ONLY each chunk.
  4. Reduce: ask the model to synthesise all partial answers into one final answer.

Usage:
    result_text = await chunk_and_reduce(provider, request, provider_context_window)
"""

from __future__ import annotations

import asyncio
import re
from copy import deepcopy

from a1.common.logging import get_logger
from a1.common.tokens import count_tokens
from a1.proxy.request_models import ChatCompletionRequest, MessageInput

log = get_logger("chunking")

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_CHUNK_OVERLAP_TOKENS = 200      # overlap between adjacent chunks to preserve context
_MAX_PARALLEL_CHUNKS = 6         # concurrent provider calls during Map phase
_OUTPUT_RESERVE_TOKENS = 1024    # tokens reserved for model output per chunk call
_SYSTEM_RESERVE_TOKENS = 512     # tokens reserved for system prompt injection


# ---------------------------------------------------------------------------
# Text splitting
# ---------------------------------------------------------------------------

def _split_paragraphs(text: str) -> list[str]:
    """Split text on double-newlines; fall back to single newlines."""
    parts = re.split(r"\n\n+", text)
    if len(parts) == 1:
        parts = text.split("\n")
    return [p for p in parts if p.strip()]


def split_into_chunks(text: str, max_tokens: int, overlap_tokens: int | None = None) -> list[str]:
    """Split `text` into chunks of at most `max_tokens` with `overlap_tokens` overlap.

    Splits along paragraph/sentence boundaries where possible.
    """
    if overlap_tokens is None:
        overlap_tokens = min(_CHUNK_OVERLAP_TOKENS, max_tokens // 8)  # overlap ≤ 12.5% of chunk

    if count_tokens(text) <= max_tokens:
        return [text]

    paragraphs = _split_paragraphs(text)
    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for para in paragraphs:
        para_tokens = count_tokens(para)

        # Single paragraph bigger than a chunk — force-split by sentence
        if para_tokens > max_tokens:
            sentences = re.split(r"(?<=[.!?])\s+", para)
            for sent in sentences:
                sent_tokens = count_tokens(sent)
                if current_tokens + sent_tokens > max_tokens and current_parts:
                    chunks.append("\n".join(current_parts))
                    # keep overlap: drop oldest parts until under overlap budget
                    while current_parts and current_tokens > overlap_tokens:
                        removed = current_parts.pop(0)
                        current_tokens -= count_tokens(removed)
                current_parts.append(sent)
                current_tokens += sent_tokens
            continue

        if current_tokens + para_tokens > max_tokens and current_parts:
            chunks.append("\n\n".join(current_parts))
            # Keep overlap
            while current_parts and current_tokens > overlap_tokens:
                removed = current_parts.pop(0)
                current_tokens -= count_tokens(removed)

        current_parts.append(para)
        current_tokens += para_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks or [text]


# ---------------------------------------------------------------------------
# MapReduce pipeline
# ---------------------------------------------------------------------------

def _extract_query(messages: list[MessageInput]) -> str:
    """Extract the user's query (last non-empty user message)."""
    for msg in reversed(messages):
        if msg.role == "user" and msg.content:
            return msg.content.strip()
    return ""


def _find_largest_content(messages: list[MessageInput]) -> tuple[int, str]:
    """Return (message_index, content) of the message with the most tokens."""
    best_idx, best_tokens, best_content = -1, 0, ""
    for i, msg in enumerate(messages):
        content = msg.content or ""
        t = count_tokens(content)
        if t > best_tokens:
            best_idx, best_tokens, best_content = i, t, content
    return best_idx, best_content


async def _map_chunk(
    provider,
    base_messages: list[MessageInput],
    chunk_content: str,
    chunk_idx: int,
    total_chunks: int,
    query: str,
    model: str,
    max_tokens: int,
) -> str:
    """Run one Map call: answer `query` using only this chunk."""
    chunk_prompt = (
        f"[Document chunk {chunk_idx + 1}/{total_chunks}]\n\n"
        f"{chunk_content}\n\n"
        f"---\n"
        f"Using ONLY the content in this chunk, answer the following as completely as possible:\n"
        f"{query}\n\n"
        f"If this chunk does not contain relevant information, say 'No relevant information in this chunk.'"
    )
    req = ChatCompletionRequest(
        model=model,
        messages=[
            *[m for m in base_messages if m.role == "system"],
            MessageInput(role="user", content=chunk_prompt),
        ],
        max_tokens=max_tokens,
        temperature=0.1,
    )
    try:
        resp = await provider.complete(req)
        return resp.choices[0].message.content or "(empty)"
    except Exception as e:
        log.warning(f"[chunking] chunk {chunk_idx} failed: {e}")
        return f"(chunk {chunk_idx + 1} failed: {e})"


async def _reduce(
    provider,
    partial_answers: list[str],
    query: str,
    system_messages: list[MessageInput],
    model: str,
    max_tokens: int,
) -> str:
    """Reduce: synthesise all partial answers into one final answer."""
    answers_text = "\n\n---\n\n".join(
        f"[Partial answer {i + 1}]\n{ans}"
        for i, ans in enumerate(partial_answers)
        if "No relevant information" not in ans
    )
    if not answers_text:
        answers_text = "\n\n---\n\n".join(
            f"[Partial answer {i + 1}]\n{ans}" for i, ans in enumerate(partial_answers)
        )

    reduce_prompt = (
        f"The following are partial answers to the question:\n"
        f"\"{query}\"\n\n"
        f"Each partial answer was derived from one chunk of a large document:\n\n"
        f"{answers_text}\n\n"
        f"---\n"
        f"Synthesise all partial answers into a single, comprehensive, coherent final answer. "
        f"Eliminate duplicates, resolve conflicts, and present the best combined answer."
    )
    req = ChatCompletionRequest(
        model=model,
        messages=[
            *system_messages,
            MessageInput(role="user", content=reduce_prompt),
        ],
        max_tokens=max_tokens,
        temperature=0.2,
    )
    resp = await provider.complete(req)
    return resp.choices[0].message.content or "(no response)"


async def chunk_and_reduce(
    provider,
    request: ChatCompletionRequest,
    provider_context_window: int,
) -> str:
    """Full MapReduce pipeline. Returns the final synthesised answer text."""
    messages = list(request.messages)
    query = _extract_query(messages)
    system_msgs = [m for m in messages if m.role == "system"]

    # Tokens available per chunk call (window minus system + overhead + output)
    system_tokens = sum(count_tokens(m.content or "") for m in system_msgs)
    chunk_max = provider_context_window - system_tokens - _OUTPUT_RESERVE_TOKENS - _SYSTEM_RESERVE_TOKENS - 256
    chunk_max = max(chunk_max, 1000)  # safety floor

    # Find and split the largest message
    large_idx, large_content = _find_largest_content(messages)
    chunks = split_into_chunks(large_content, chunk_max)

    log.info(
        f"[chunking] content={count_tokens(large_content)}tok "
        f"window={provider_context_window} chunks={len(chunks)} chunk_max={chunk_max}tok"
    )

    if len(chunks) == 1:
        # Content fits after all (edge case) — just run normally
        resp = await provider.complete(request)
        return resp.choices[0].message.content or ""

    # Map phase — parallel, capped at _MAX_PARALLEL_CHUNKS
    sem = asyncio.Semaphore(_MAX_PARALLEL_CHUNKS)

    async def _bounded_map(idx: int, chunk: str) -> str:
        async with sem:
            return await _map_chunk(
                provider=provider,
                base_messages=messages,
                chunk_content=chunk,
                chunk_idx=idx,
                total_chunks=len(chunks),
                query=query,
                model=request.model,
                max_tokens=request.max_tokens or 1000,
            )

    partial_answers = await asyncio.gather(*[_bounded_map(i, c) for i, c in enumerate(chunks)])

    log.info(f"[chunking] Map phase done — {len(partial_answers)} partial answers")

    # Reduce phase
    final = await _reduce(
        provider=provider,
        partial_answers=list(partial_answers),
        query=query,
        system_messages=system_msgs,
        model=request.model,
        max_tokens=request.max_tokens or 1000,
    )

    log.info("[chunking] Reduce phase done")
    return final


# ---------------------------------------------------------------------------
# Context overflow detection
# ---------------------------------------------------------------------------

def needs_chunking(total_tokens: int, provider_context_window: int, threshold: float = 0.85) -> bool:
    """Return True if token count exceeds threshold * context_window."""
    return total_tokens > int(provider_context_window * threshold)
