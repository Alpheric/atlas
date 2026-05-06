"""OpenAI-compatible Files API: POST/GET/DELETE /v1/files.

Implements the OpenAI Files API spec:
  POST   /v1/files              — upload a file (multipart/form-data)
  GET    /v1/files              — list all files (optional ?purpose= filter)
  GET    /v1/files/{file_id}    — retrieve file metadata
  DELETE /v1/files/{file_id}    — delete file and its stored content
  GET    /v1/files/{file_id}/content — download raw file bytes

Files are stored on disk under settings.upload_dir/{file_id}/{filename}.
Metadata is persisted in the uploaded_files DB table.

Supported purposes (matching OpenAI):
  assistants, batch, fine-tune, vision, user_data, evals
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import delete, select

from a1.common.auth import verify_api_key
from a1.common.logging import get_logger
from a1.db.engine import async_session
from a1.db.models import UploadedFile
from config.settings import settings

log = get_logger("proxy.files")
router = APIRouter()

_ALLOWED_PURPOSES = {
    "assistants", "batch", "fine-tune", "vision", "user_data", "evals",
}

_MIME_FALLBACK = "application/octet-stream"


# ---------------------------------------------------------------------------
# Response schemas
# ---------------------------------------------------------------------------

class FileObject(BaseModel):
    id: str
    object: str = "file"
    bytes: int
    created_at: int
    filename: str
    purpose: str


class FileListResponse(BaseModel):
    object: str = "list"
    data: list[FileObject]


class DeleteResponse(BaseModel):
    id: str
    object: str = "file"
    deleted: bool = True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_file_object(row: UploadedFile) -> FileObject:
    return FileObject(
        id=row.id,
        bytes=row.bytes_,
        created_at=int(row.created_at.timestamp()),
        filename=row.filename,
        purpose=row.purpose,
    )


def _upload_root() -> Path:
    p = Path(settings.upload_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _file_dir(file_id: str) -> Path:
    d = _upload_root() / file_id
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/v1/files", response_model=FileObject)
async def upload_file(
    file: UploadFile,
    purpose: str = Form(...),
    _api_key: str = Depends(verify_api_key),
) -> Any:
    """Upload a file via multipart/form-data with fields: file + purpose."""
    if not purpose.strip():
        raise HTTPException(status_code=400, detail="'purpose' field is required.")
    if purpose not in _ALLOWED_PURPOSES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid purpose '{purpose}'. Must be one of: {sorted(_ALLOWED_PURPOSES)}",
        )

    content = await file.read()
    if len(content) == 0:
        raise HTTPException(status_code=400, detail="Uploaded file is empty.")
    if len(content) > settings.upload_max_bytes:
        max_mb = settings.upload_max_bytes // (1024 * 1024)
        raise HTTPException(status_code=413, detail=f"File exceeds {max_mb} MB limit.")

    file_id = f"file-{uuid.uuid4().hex}"
    filename = file.filename or "upload"
    mime_type = file.content_type or _MIME_FALLBACK

    # Write to disk
    dest = _file_dir(file_id) / filename
    dest.write_bytes(content)

    async with async_session() as db:
        async with db.begin():
            row = UploadedFile(
                id=file_id,
                filename=filename,
                purpose=purpose,
                bytes_=len(content),
                mime_type=mime_type,
                storage_path=str(dest),
                workspace_id=None,
            )
            db.add(row)

    log.info(f"[files] uploaded {file_id} name={filename} purpose={purpose} size={len(content)}B")
    return FileObject(
        id=file_id,
        bytes=len(content),
        created_at=int(__import__("time").time()),
        filename=filename,
        purpose=purpose,
    )


@router.get("/v1/files", response_model=FileListResponse)
async def list_files(
    purpose: str | None = None,
    limit: int = 100,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        q = select(UploadedFile).order_by(UploadedFile.created_at.desc()).limit(min(limit, 10000))
        if purpose:
            q = q.where(UploadedFile.purpose == purpose)
        rows = (await db.execute(q)).scalars().all()
    return FileListResponse(data=[_to_file_object(r) for r in rows])


@router.get("/v1/files/{file_id}", response_model=FileObject)
async def retrieve_file(
    file_id: str,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        row = (await db.execute(select(UploadedFile).where(UploadedFile.id == file_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"File '{file_id}' not found.")
    return _to_file_object(row)


@router.delete("/v1/files/{file_id}", response_model=DeleteResponse)
async def delete_file(
    file_id: str,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        row = (await db.execute(select(UploadedFile).where(UploadedFile.id == file_id))).scalar_one_or_none()
        if not row:
            raise HTTPException(status_code=404, detail=f"File '{file_id}' not found.")
        storage_path = row.storage_path
        await db.execute(delete(UploadedFile).where(UploadedFile.id == file_id))
        await db.commit()

    # Remove from disk
    try:
        p = Path(storage_path)
        if p.exists():
            p.unlink()
        parent = p.parent
        if parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except Exception as e:
        log.warning(f"[files] could not remove {storage_path}: {e}")

    log.info(f"[files] deleted {file_id}")
    return DeleteResponse(id=file_id)


@router.get("/v1/files/{file_id}/content")
async def download_file(
    file_id: str,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        row = (await db.execute(select(UploadedFile).where(UploadedFile.id == file_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"File '{file_id}' not found.")

    p = Path(row.storage_path)
    if not p.exists():
        raise HTTPException(status_code=404, detail="File content not found on disk.")

    return FileResponse(
        path=str(p),
        filename=row.filename,
        media_type=row.mime_type,
    )
