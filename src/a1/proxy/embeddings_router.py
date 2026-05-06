"""OpenAI-compatible embeddings endpoint: POST /v1/embeddings.

Routing:
  - Local models / small requests → Ollama nomic-embed-text (768 dims)
  - text-embedding-3-large or Ollama unavailable → Vertex gemini-embedding-001 (3072 dims)

OpenAI model aliases accepted:
  text-embedding-ada-002, text-embedding-3-small  → nomic-embed-text (local)
  text-embedding-3-large                          → gemini-embedding-001 (Vertex)
  nomic-embed-text, nomic-embed-text:latest       → nomic-embed-text (local)
  gemini-embedding-001, gemini-embedding-2         → Vertex

Batch input: "input" may be a string or list[str]. All items embedded in one call.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from a1.common.auth import verify_api_key
from a1.common.logging import get_logger
from config.settings import settings

log = get_logger("proxy.embeddings")
router = APIRouter()

# ---------------------------------------------------------------------------
# Model routing table
# ---------------------------------------------------------------------------

_OLLAMA_MODELS = {
    "text-embedding-ada-002",
    "text-embedding-3-small",
    "nomic-embed-text",
    "nomic-embed-text:latest",
    "nomic",
}

_VERTEX_MODELS = {
    "text-embedding-3-large",
    "gemini-embedding-001",
    "gemini-embedding-2",
    "gemini-embedding-2-preview",
}

_OLLAMA_MODEL = "nomic-embed-text:latest"   # 768 dims
_VERTEX_MODEL = "gemini-embedding-001"       # 3072 dims


def _route(requested_model: str) -> str:
    """Return 'ollama' or 'vertex' for the requested model name."""
    m = requested_model.lower()
    if m in _VERTEX_MODELS or "large" in m:
        return "vertex"
    return "ollama"


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------

class EmbeddingRequest(BaseModel):
    model: str = "text-embedding-3-small"
    input: str | list[str]
    encoding_format: str = "float"   # "float" | "base64"  (base64 not supported, ignored)
    dimensions: int | None = None    # ignored — determined by model


class EmbeddingObject(BaseModel):
    object: str = "embedding"
    embedding: list[float]
    index: int


class EmbeddingUsage(BaseModel):
    prompt_tokens: int
    total_tokens: int


class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: list[EmbeddingObject]
    model: str
    usage: EmbeddingUsage


# ---------------------------------------------------------------------------
# Provider implementations
# ---------------------------------------------------------------------------

async def _embed_ollama(texts: list[str]) -> list[list[float]]:
    """Call Ollama /api/embed — supports batch input natively."""
    # Pick a healthy Ollama server
    servers: list[str] = getattr(settings, "ollama_servers", None) or ["http://10.0.0.9:11434"]
    server = servers[0]

    async with httpx.AsyncClient(base_url=server, timeout=60.0) as client:
        resp = await client.post(
            "/api/embed",
            json={"model": _OLLAMA_MODEL, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()

    embeddings: list[list[float]] = data.get("embeddings", [])
    if not embeddings:
        raise ValueError("Ollama returned no embeddings")
    return embeddings


async def _embed_vertex_one(client: httpx.AsyncClient, text: str, api_key: str) -> list[float]:
    """Embed a single text via Gemini API key (embedContent endpoint)."""
    resp = await client.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{_VERTEX_MODEL}:embedContent",
        params={"key": api_key},
        json={"content": {"parts": [{"text": text}]}},
        timeout=30.0,
    )
    resp.raise_for_status()
    values: list[float] = resp.json().get("embedding", {}).get("values", [])
    if not values:
        raise ValueError("Vertex returned no embedding values")
    return values


async def _embed_vertex(texts: list[str]) -> list[list[float]]:
    """Batch embed via Vertex gemini-embedding-001 using concurrency."""
    api_key = settings.vertex_api_key
    if not api_key:
        raise HTTPException(status_code=503, detail="Vertex API key not configured.")

    async with httpx.AsyncClient() as client:
        results = await asyncio.gather(
            *[_embed_vertex_one(client, t, api_key) for t in texts],
            return_exceptions=True,
        )

    embeddings: list[list[float]] = []
    for i, r in enumerate(results):
        if isinstance(r, Exception):
            raise HTTPException(status_code=502, detail=f"Vertex embedding failed for item {i}: {r}")
        embeddings.append(r)  # type: ignore[arg-type]
    return embeddings


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------

@router.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(
    body: EmbeddingRequest,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    t0 = time.time()

    # Normalise input to list[str]
    texts: list[str] = [body.input] if isinstance(body.input, str) else list(body.input)
    if not texts:
        raise HTTPException(status_code=400, detail="input must not be empty.")
    if len(texts) > 2048:
        raise HTTPException(status_code=400, detail="Maximum 2048 inputs per request.")

    provider = _route(body.model)
    resolved_model: str

    # Try preferred provider, fall back to the other
    try:
        if provider == "ollama":
            embeddings = await _embed_ollama(texts)
            resolved_model = _OLLAMA_MODEL
        else:
            embeddings = await _embed_vertex(texts)
            resolved_model = _VERTEX_MODEL
    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"[embeddings] {provider} failed ({e}), trying fallback")
        try:
            if provider == "ollama":
                embeddings = await _embed_vertex(texts)
                resolved_model = _VERTEX_MODEL
            else:
                embeddings = await _embed_ollama(texts)
                resolved_model = _OLLAMA_MODEL
        except Exception as e2:
            raise HTTPException(status_code=502, detail=f"Both embedding providers failed: {e2}")

    # Rough token estimate (≈ 0.75 tokens/char)
    total_chars = sum(len(t) for t in texts)
    tokens = max(1, int(total_chars * 0.75))

    log.info(
        f"[embeddings] model={resolved_model} provider={provider} "
        f"n={len(texts)} dims={len(embeddings[0])} "
        f"latency={int((time.time()-t0)*1000)}ms"
    )

    return EmbeddingResponse(
        data=[EmbeddingObject(embedding=emb, index=i) for i, emb in enumerate(embeddings)],
        model=resolved_model,
        usage=EmbeddingUsage(prompt_tokens=tokens, total_tokens=tokens),
    )
