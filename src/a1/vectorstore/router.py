"""OpenAI-compatible Vector Stores API.

Endpoints:
  POST   /v1/vector_stores                        — create a store
  GET    /v1/vector_stores                        — list stores
  GET    /v1/vector_stores/{store_id}             — retrieve store info
  DELETE /v1/vector_stores/{store_id}             — delete store + all chunks
  POST   /v1/vector_stores/{store_id}/files       — add a file (chunks + embeds)
  GET    /v1/vector_stores/{store_id}/files       — list files in store
  DELETE /v1/vector_stores/{store_id}/files/{file_id} — remove file's chunks
  POST   /v1/vector_stores/{store_id}/search      — semantic search (top-k)

Embedding:
  Delegates to the existing /v1/embeddings infrastructure.
  Default model: nomic-embed-text (768 dims, local Ollama).
  Use "text-embedding-3-large" for 3072-dim Gemini embeddings.

Chunking:
  Large files are split into overlapping chunks using the chunker module.
  Default chunk size: 512 tokens with 64-token overlap.
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import delete, select, text

from a1.common.auth import verify_api_key
from a1.common.logging import get_logger
from a1.common.tokens import count_tokens
from a1.db.engine import async_session
from a1.db.models import UploadedFile, VectorChunk, VectorStore
from config.settings import settings

log = get_logger("vectorstore")
router = APIRouter()

_DEFAULT_EMBED_MODEL = "text-embedding-3-small"   # → nomic-embed-text (768 dims, local)
_CHUNK_TOKENS = 512
_CHUNK_OVERLAP = 64
_EMBED_BATCH = 64   # max texts per embedding call


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class CreateStoreRequest(BaseModel):
    name: str
    metadata: dict | None = None


class AddFileRequest(BaseModel):
    file_id: str
    embedding_model: str = _DEFAULT_EMBED_MODEL
    chunk_size: int = _CHUNK_TOKENS
    chunk_overlap: int = _CHUNK_OVERLAP


class SearchRequest(BaseModel):
    query: str
    top_k: int = 5
    embedding_model: str = _DEFAULT_EMBED_MODEL


class ChunkResult(BaseModel):
    chunk_id: str
    file_id: str | None
    filename: str | None
    chunk_index: int
    content: str
    score: float


class SearchResponse(BaseModel):
    object: str = "list"
    data: list[ChunkResult]
    store_id: str


class StoreObject(BaseModel):
    id: str
    object: str = "vector_store"
    name: str
    created_at: int
    file_count: int = 0
    chunk_count: int = 0


class FileInStore(BaseModel):
    file_id: str
    filename: str | None
    chunk_count: int
    created_at: int


# ---------------------------------------------------------------------------
# Embedding helper (calls local /v1/embeddings)
# ---------------------------------------------------------------------------

async def _embed(texts: list[str], model: str = _DEFAULT_EMBED_MODEL) -> list[list[float]]:
    """Embed texts by calling the embeddings router logic directly (no HTTP round-trip)."""
    from a1.proxy.embeddings_router import _embed_ollama, _embed_vertex, _route

    provider = _route(model)
    try:
        if provider == "ollama":
            return await _embed_ollama(texts)
        else:
            return await _embed_vertex(texts)
    except Exception as e:
        # Fallback to the other provider
        log.warning(f"[vectorstore] embed {provider} failed ({e}), trying fallback")
        if provider == "ollama":
            return await _embed_vertex(texts)
        return await _embed_ollama(texts)


# ---------------------------------------------------------------------------
# Text chunking (reuse chunker's sentence splitter)
# ---------------------------------------------------------------------------

def _chunk_text(text: str, max_tokens: int = _CHUNK_TOKENS, overlap: int = _CHUNK_OVERLAP) -> list[str]:
    from a1.chunking.chunker import split_into_chunks
    return split_into_chunks(text, max_tokens=max_tokens, overlap_tokens=overlap)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/v1/vector_stores", response_model=StoreObject)
async def create_store(
    body: CreateStoreRequest,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    store_id = f"vs-{uuid.uuid4().hex[:16]}"
    async with async_session() as db:
        async with db.begin():
            store = VectorStore(
                id=store_id,
                name=body.name,
                metadata_=body.metadata,
            )
            db.add(store)
    log.info(f"[vectorstore] created store {store_id} name={body.name!r}")
    return StoreObject(id=store_id, name=body.name, created_at=int(time.time()))


@router.get("/v1/vector_stores", response_model=list[StoreObject])
async def list_stores(_api_key: str = Depends(verify_api_key)) -> Any:
    from sqlalchemy import func
    async with async_session() as db:
        rows = (await db.execute(
            select(
                VectorStore.id, VectorStore.name, VectorStore.created_at,
                func.count(VectorChunk.id).label("chunk_count"),
            )
            .outerjoin(VectorChunk, VectorChunk.store_id == VectorStore.id)
            .group_by(VectorStore.id)
            .order_by(VectorStore.created_at.desc())
        )).all()
    return [
        StoreObject(
            id=r.id, name=r.name,
            created_at=int(r.created_at.timestamp()),
            chunk_count=r.chunk_count,
        )
        for r in rows
    ]


@router.get("/v1/vector_stores/{store_id}", response_model=StoreObject)
async def get_store(store_id: str, _api_key: str = Depends(verify_api_key)) -> Any:
    from sqlalchemy import func
    async with async_session() as db:
        row = (await db.execute(
            select(
                VectorStore.id, VectorStore.name, VectorStore.created_at,
                func.count(VectorChunk.id).label("chunk_count"),
            )
            .outerjoin(VectorChunk, VectorChunk.store_id == VectorStore.id)
            .where(VectorStore.id == store_id)
            .group_by(VectorStore.id)
        )).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"Vector store '{store_id}' not found.")
    return StoreObject(
        id=row.id, name=row.name,
        created_at=int(row.created_at.timestamp()),
        chunk_count=row.chunk_count,
    )


@router.delete("/v1/vector_stores/{store_id}")
async def delete_store(store_id: str, _api_key: str = Depends(verify_api_key)) -> Any:
    async with async_session() as db:
        row = (await db.execute(
            select(VectorStore).where(VectorStore.id == store_id)
        )).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail=f"Vector store '{store_id}' not found.")
        await db.execute(delete(VectorStore).where(VectorStore.id == store_id))
        await db.commit()
    log.info(f"[vectorstore] deleted store {store_id}")
    return {"id": store_id, "object": "vector_store", "deleted": True}


@router.post("/v1/vector_stores/{store_id}/files")
async def add_file_to_store(
    store_id: str,
    body: AddFileRequest,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    """Chunk a previously-uploaded file, embed each chunk, persist to vector store."""
    async with async_session() as db:
        store = (await db.execute(
            select(VectorStore).where(VectorStore.id == store_id)
        )).scalar_one_or_none()
        if not store:
            raise HTTPException(status_code=404, detail=f"Vector store '{store_id}' not found.")

        file_row = (await db.execute(
            select(UploadedFile).where(UploadedFile.id == body.file_id)
        )).scalar_one_or_none()
        if not file_row:
            raise HTTPException(status_code=404, detail=f"File '{body.file_id}' not found.")

    # Read file content
    file_path = Path(file_row.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File content not found on disk.")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Cannot read file as text: {e}")

    # Chunk
    chunks = _chunk_text(content, max_tokens=body.chunk_size, overlap=body.chunk_overlap)
    log.info(f"[vectorstore] file={body.file_id} chunks={len(chunks)} model={body.embedding_model}")

    # Embed in batches
    all_embeddings: list[list[float]] = []
    for i in range(0, len(chunks), _EMBED_BATCH):
        batch = chunks[i:i + _EMBED_BATCH]
        try:
            embs = await _embed(batch, model=body.embedding_model)
            all_embeddings.extend(embs)
        except Exception as e:
            raise HTTPException(status_code=502, detail=f"Embedding failed for batch {i}: {e}")

    # Persist
    async with async_session() as db:
        async with db.begin():
            for idx, (chunk_text, emb) in enumerate(zip(chunks, all_embeddings)):
                db.add(VectorChunk(
                    store_id=store_id,
                    file_id=body.file_id,
                    filename=file_row.filename,
                    chunk_index=idx,
                    content=chunk_text,
                    embedding=emb,
                    model=body.embedding_model,
                ))

    log.info(f"[vectorstore] stored {len(chunks)} chunks from file={body.file_id} in store={store_id}")
    return {
        "store_id": store_id,
        "file_id": body.file_id,
        "filename": file_row.filename,
        "chunks_created": len(chunks),
        "embedding_model": body.embedding_model,
    }


@router.get("/v1/vector_stores/{store_id}/files", response_model=list[FileInStore])
async def list_files_in_store(
    store_id: str,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        rows = (await db.execute(
            select(VectorChunk.file_id, VectorChunk.filename, VectorChunk.created_at)
            .where(VectorChunk.store_id == store_id)
            .distinct(VectorChunk.file_id)
            .order_by(VectorChunk.file_id, VectorChunk.created_at)
        )).all()

        # Count chunks per file
        file_counts: dict[str, int] = {}
        file_meta: dict[str, tuple] = {}
        for row in rows:
            fid = row.file_id or ""
            if fid not in file_counts:
                file_meta[fid] = (row.filename, row.created_at)
            file_counts[fid] = file_counts.get(fid, 0) + 1

        # Re-query for accurate counts
        all_chunks = (await db.execute(
            select(VectorChunk.file_id, VectorChunk.filename, VectorChunk.created_at)
            .where(VectorChunk.store_id == store_id)
            .order_by(VectorChunk.created_at)
        )).all()

    file_chunks: dict[str, list] = {}
    for row in all_chunks:
        fid = row.file_id or ""
        if fid not in file_chunks:
            file_chunks[fid] = []
        file_chunks[fid].append(row)

    return [
        FileInStore(
            file_id=fid,
            filename=chunks[0].filename,
            chunk_count=len(chunks),
            created_at=int(chunks[0].created_at.timestamp()),
        )
        for fid, chunks in file_chunks.items()
    ]


@router.delete("/v1/vector_stores/{store_id}/files/{file_id}")
async def remove_file_from_store(
    store_id: str,
    file_id: str,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        result = await db.execute(
            delete(VectorChunk).where(
                VectorChunk.store_id == store_id,
                VectorChunk.file_id == file_id,
            )
        )
        await db.commit()
    deleted = result.rowcount
    log.info(f"[vectorstore] removed {deleted} chunks of file={file_id} from store={store_id}")
    return {"store_id": store_id, "file_id": file_id, "chunks_deleted": deleted, "deleted": True}


@router.post("/v1/vector_stores/{store_id}/search", response_model=SearchResponse)
async def search_store(
    store_id: str,
    body: SearchRequest,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    """Embed the query and return the top-k most similar chunks."""
    # Verify store exists
    async with async_session() as db:
        store = (await db.execute(
            select(VectorStore).where(VectorStore.id == store_id)
        )).scalar_one_or_none()
        if not store:
            raise HTTPException(status_code=404, detail=f"Vector store '{store_id}' not found.")

    # Embed query
    try:
        [query_emb] = await _embed([body.query], model=body.embedding_model)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to embed query: {e}")

    # pgvector cosine similarity search
    query_vec = f"[{','.join(str(x) for x in query_emb)}]"
    top_k = min(body.top_k, 100)

    async with async_session() as db:
        rows = (await db.execute(
            text("""
                SELECT id, file_id, filename, chunk_index, content,
                       1 - (embedding <=> CAST(:qvec AS vector)) AS score
                FROM vector_chunks
                WHERE store_id = :sid
                ORDER BY embedding <=> CAST(:qvec AS vector)
                LIMIT :k
            """),
            {"sid": store_id, "qvec": query_vec, "k": top_k},
        )).fetchall()

    results = [
        ChunkResult(
            chunk_id=str(row.id),
            file_id=row.file_id,
            filename=row.filename,
            chunk_index=row.chunk_index,
            content=row.content,
            score=round(float(row.score), 4),
        )
        for row in rows
    ]
    log.info(f"[vectorstore] search store={store_id} top_k={top_k} results={len(results)}")
    return SearchResponse(data=results, store_id=store_id)
