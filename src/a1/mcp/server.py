"""Atlas MCP Server — exposes Atlas capabilities via Model Context Protocol.

Mounted at /mcp (SSE transport) so Claude Code / Cline / any MCP client can
discover and call Atlas tools without writing custom API integration.

Transport: HTTP+SSE (MCP 2024-11-05 spec)
  GET  /mcp/sse   — server-sent events stream (server→client)
  POST /mcp/messages  — JSON-RPC messages (client→server)

Claude Code config (~/.claude.json or project .mcp.json):
  {
    "mcpServers": {
      "atlas": {
        "type": "sse",
        "url": "http://localhost:8001/mcp/sse",
        "headers": {"Authorization": "Bearer sk-atlas-..."}
      }
    }
  }

Tools exposed:
  chat           — send a message to Atlas, get a completion
  search_store   — semantic search over a vector store
  embed          — create text embeddings
  list_models    — list available Atlas models
  upload_file    — upload text content as a file
  read_file      — read a previously-uploaded file
  list_files     — list uploaded files
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from a1.common.logging import get_logger

log = get_logger("mcp.server")

mcp = FastMCP(
    name="Atlas",
    instructions=(
        "Atlas is an enterprise AI middleware platform by Alpheric Technologies. "
        "Use the `chat` tool to send messages to Atlas models. "
        "Use `search_store` for semantic retrieval from a vector store. "

        "Use `embed` to create text embeddings."
    ),
)


# ---------------------------------------------------------------------------
# Tool: chat
# ---------------------------------------------------------------------------


@mcp.tool()
async def chat(
    message: str,
    model: str = "auto",
    system: str = "",
    max_tokens: int = 2000,
) -> str:
    """Send a message to Atlas and get a completion.

    Args:
        message:    The user message to send.
        model:      Atlas model to use. Options: auto, atlas-plan, atlas-code,
                    atlas-secure, atlas-infra, atlas-data, gemini-2.5-flash, etc.
        system:     Optional system prompt to prepend.
        max_tokens: Maximum tokens in the response (default 2000).

    Returns:
        The assistant's response text.
    """
    from a1.proxy.core_pipeline import CorePipelineInput, core_pipeline
    from a1.proxy.request_models import MessageInput

    messages: list[MessageInput] = []
    if system:
        messages.append(MessageInput(role="system", content=system))
    messages.append(MessageInput(role="user", content=message))

    inp = CorePipelineInput(
        request_id="mcp-chat",
        source="mcp",
        messages=messages,
        model=model,
        max_tokens=max_tokens,
        stream=False,
    )
    result = await core_pipeline.execute(inp)
    if result.error:
        return f"Error: {result.error}"
    return result.assistant_text or "(no response)"


# ---------------------------------------------------------------------------
# Tool: search_store
# ---------------------------------------------------------------------------


@mcp.tool()
async def search_store(
    store_id: str,
    query: str,
    top_k: int = 5,
    embedding_model: str = "text-embedding-3-small",
) -> str:
    """Semantic search in a vector store.

    Args:
        store_id:        Vector store ID (e.g. "vs-abc123...").
        query:           Natural language query to search for.
        top_k:           Number of results to return (max 20).
        embedding_model: Embedding model for the query vector.

    Returns:
        JSON-formatted list of matching chunks with scores.
    """
    import json

    from sqlalchemy import text

    from a1.db.engine import async_session
    from a1.proxy.embeddings_router import _embed_ollama, _embed_vertex, _route

    top_k = min(top_k, 20)
    provider = _route(embedding_model)
    try:
        if provider == "ollama":
            [emb] = await _embed_ollama([query])
        else:
            [emb] = await _embed_vertex([query])
    except Exception as e:
        return f"Embedding error: {e}"

    query_vec = f"[{','.join(str(x) for x in emb)}]"

    async with async_session() as db:
        rows = (
            await db.execute(
                text("""
                SELECT file_id, filename, chunk_index, content,
                       1 - (embedding <=> CAST(:qvec AS vector)) AS score
                FROM vector_chunks
                WHERE store_id = :sid
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :k
            """),
                {"sid": store_id, "qvec": query_vec, "k": top_k},
            )
        ).fetchall()

    results = [
        {
            "score": round(float(r.score), 4),
            "file": r.filename,
            "chunk": r.chunk_index,
            "content": r.content[:500],
        }
        for r in rows
    ]
    return json.dumps(results, indent=2)


# ---------------------------------------------------------------------------
# Tool: embed
# ---------------------------------------------------------------------------


@mcp.tool()
async def embed(
    text: str,
    model: str = "text-embedding-3-small",
) -> str:
    """Create a text embedding vector.

    Args:
        text:  The text to embed.
        model: Embedding model. "text-embedding-3-small" → nomic (768 dims, local).
               "text-embedding-3-large" → Gemini (3072 dims).

    Returns:
        JSON with the embedding vector and its dimension.
    """
    import json

    from a1.proxy.embeddings_router import _embed_ollama, _embed_vertex, _route

    provider = _route(model)
    try:
        if provider == "ollama":
            [emb] = await _embed_ollama([text])
        else:
            [emb] = await _embed_vertex([text])
        return json.dumps(
            {
                "dims": len(emb),
                "embedding": emb[:8],
                "truncated": True,
                "note": "First 8 dims shown. Full vector available via /v1/embeddings.",
            }
        )
    except Exception as e:
        return f"Embedding error: {e}"


# ---------------------------------------------------------------------------
# Tool: run_code  (DISABLED — security: unrestricted OS execution)
# ---------------------------------------------------------------------------
# run_code is intentionally NOT exposed on the public MCP endpoint.
# The code_interpreter runs as the server OS user with no sandbox (no Docker,
# no seccomp, no builtins restriction).  Exposing it via MCP would grant any
# API key holder full shell access to the host.
#
# To re-enable: add proper sandboxing (Docker/gVisor) and restrict to admin
# keys only, then restore the @mcp.tool() decorator below.
#
# @mcp.tool()
# async def run_code(code: str) -> str:
#     from a1.tools.code_interpreter import run_code as _run
#     return await _run(code)


# ---------------------------------------------------------------------------
# Tool: list_models
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_models() -> str:
    """List all Atlas models and providers currently available.

    Returns:
        JSON list of model names, providers, and context windows.
    """
    import json

    from a1.providers.registry import provider_registry

    models = []
    for name, provider in provider_registry.healthy_providers.items():
        for m in provider.list_models():
            models.append(
                {
                    "id": m.name,
                    "provider": name,
                    "context_window": m.context_window,
                }
            )
    # Add Atlas virtual models
    atlas_models = [
        "atlas-plan",
        "atlas-code",
        "atlas-secure",
        "atlas-infra",
        "atlas-data",
        "atlas-books",
        "atlas-audit",
        "auto",
    ]
    for am in atlas_models:
        models.insert(0, {"id": am, "provider": "atlas", "context_window": 200000})
    return json.dumps(models, indent=2)


# ---------------------------------------------------------------------------
# Tool: upload_file
# ---------------------------------------------------------------------------


@mcp.tool()
async def upload_file(
    filename: str,
    content: str,
    purpose: str = "assistants",
) -> str:
    """Upload text content as a file to Atlas file storage.

    Args:
        filename: Name for the file (e.g. "document.txt").
        content:  Text content to store.
        purpose:  Purpose tag. Options: assistants, batch, fine-tune, vision.

    Returns:
        The file ID and metadata as JSON.
    """
    import json
    import time
    import uuid
    from pathlib import Path

    from a1.db.engine import async_session
    from a1.db.models import UploadedFile
    from config.settings import settings

    # Sanitize filename — strip any path components to prevent traversal
    safe_filename = Path(filename).name  # drops all directory parts
    if not safe_filename or safe_filename in (".", ".."):
        safe_filename = "upload.txt"

    file_id = f"file-{uuid.uuid4().hex}"
    upload_root = Path(settings.upload_dir)
    upload_root.mkdir(parents=True, exist_ok=True)
    file_dir = upload_root / file_id
    file_dir.mkdir(parents=True, exist_ok=True)
    dest = file_dir / safe_filename

    # Final safety check — dest must be inside upload_root
    dest = dest.resolve()
    if not str(dest).startswith(str(upload_root.resolve())):
        return json.dumps({"error": "Invalid filename"})

    content_bytes = content.encode("utf-8")
    dest.write_bytes(content_bytes)

    async with async_session() as db:
        async with db.begin():
            db.add(
                UploadedFile(
                    id=file_id,
                    filename=safe_filename,
                    purpose=purpose,
                    bytes_=len(content_bytes),
                    mime_type="text/plain",
                    storage_path=str(dest),
                )
            )

    return json.dumps(
        {
            "id": file_id,
            "filename": safe_filename,
            "purpose": purpose,
            "bytes": len(content_bytes),
            "created_at": int(time.time()),
        }
    )


# ---------------------------------------------------------------------------
# Tool: read_file
# ---------------------------------------------------------------------------


@mcp.tool()
async def read_file(file_id: str) -> str:
    """Read the content of a previously uploaded file.

    Args:
        file_id: The file ID returned by upload_file (e.g. "file-abc123...").

    Returns:
        The file content as text (first 32KB).
    """
    from pathlib import Path

    from sqlalchemy import select

    from a1.db.engine import async_session
    from a1.db.models import UploadedFile

    async with async_session() as db:
        row = (
            await db.execute(select(UploadedFile).where(UploadedFile.id == file_id))
        ).scalar_one_or_none()

    if not row:
        return f"Error: file '{file_id}' not found."

    p = Path(row.storage_path)
    if not p.exists():
        return "Error: file content not found on disk."

    content = p.read_text(encoding="utf-8", errors="replace")
    if len(content) > 32768:
        content = content[:32768] + f"\n... [truncated at 32KB, full size={row.bytes_}B]"
    return content


# ---------------------------------------------------------------------------
# Tool: list_files
# ---------------------------------------------------------------------------


@mcp.tool()
async def list_files(purpose: str = "") -> str:
    """List uploaded files.

    Args:
        purpose: Optional filter by purpose (assistants, batch, fine-tune, etc.)

    Returns:
        JSON list of files with IDs, names, and sizes.
    """
    import json

    from sqlalchemy import select

    from a1.db.engine import async_session
    from a1.db.models import UploadedFile

    async with async_session() as db:
        q = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(100)
        if purpose:
            q = q.where(UploadedFile.purpose == purpose)
        rows = (await db.execute(q)).scalars().all()

    return json.dumps(
        [
            {
                "id": r.id,
                "filename": r.filename,
                "purpose": r.purpose,
                "bytes": r.bytes_,
                "created_at": int(r.created_at.timestamp()),
            }
            for r in rows
        ],
        indent=2,
    )


# ---------------------------------------------------------------------------
# Resource: list vector stores
# ---------------------------------------------------------------------------


@mcp.resource("atlas://vector_stores")
async def list_vector_stores() -> str:
    """List all vector stores available in Atlas."""
    import json

    from sqlalchemy import func, select

    from a1.db.engine import async_session
    from a1.db.models import VectorChunk, VectorStore

    async with async_session() as db:
        rows = (
            await db.execute(
                select(
                    VectorStore.id,
                    VectorStore.name,
                    VectorStore.created_at,
                    func.count(VectorChunk.id).label("chunk_count"),
                )
                .outerjoin(VectorChunk, VectorChunk.store_id == VectorStore.id)
                .group_by(VectorStore.id)
                .order_by(VectorStore.created_at.desc())
            )
        ).all()

    return json.dumps(
        [{"id": r.id, "name": r.name, "chunks": r.chunk_count} for r in rows], indent=2
    )
