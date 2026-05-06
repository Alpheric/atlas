"""OpenAI-compatible Batch API — POST /v1/batches.

Input file format (JSONL, one request per line):
  {"custom_id": "req-1", "method": "POST", "url": "/v1/chat/completions",
   "body": {"model": "auto", "messages": [{"role": "user", "content": "Hello"}]}}

Output file format (JSONL):
  {"id": "breq-...", "custom_id": "req-1",
   "response": {"status_code": 200, "body": {...}}, "error": null}

Endpoints:
  POST   /v1/batches                     — create a batch
  GET    /v1/batches                     — list batches
  GET    /v1/batches/{batch_id}          — get batch status
  POST   /v1/batches/{batch_id}/cancel   — cancel in-progress batch

Results are written to a file accessible via GET /v1/files/{output_file_id}/content.

Processing:
  - Up to A1_BATCH_MAX_PARALLEL (default 5) concurrent requests
  - Status updates: validating → in_progress → finalizing → completed | failed
  - Each line in output has the same custom_id as the input
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, update

from a1.common.auth import verify_api_key
from a1.common.logging import get_logger
from a1.common.tz import now_ist
from a1.db.engine import async_session
from a1.db.models import Batch, UploadedFile
from config.settings import settings

log = get_logger("proxy.batch")
router = APIRouter()

_MAX_PARALLEL: int = getattr(settings, "batch_max_parallel", 5)
_MAX_REQUESTS: int = getattr(settings, "batch_max_requests", 50_000)
_SUPPORTED_ENDPOINTS = {"/v1/chat/completions"}

# Track running tasks so we can cancel them
_active_tasks: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------


class CreateBatchRequest(BaseModel):
    input_file_id: str
    endpoint: str = "/v1/chat/completions"
    completion_window: str = "24h"
    metadata: dict | None = None


class BatchRequestCounts(BaseModel):
    total: int
    completed: int
    failed: int


class BatchObject(BaseModel):
    id: str
    object: str = "batch"
    endpoint: str
    input_file_id: str
    output_file_id: str | None = None
    error_file_id: str | None = None
    completion_window: str
    status: str
    request_counts: BatchRequestCounts
    created_at: int
    in_progress_at: int | None = None
    finalizing_at: int | None = None
    completed_at: int | None = None
    failed_at: int | None = None
    cancelled_at: int | None = None
    expires_at: int | None = None
    metadata: dict | None = None


def _to_batch_obj(row: Batch) -> BatchObject:
    def _ts(dt) -> int | None:
        return int(dt.timestamp()) if dt else None

    return BatchObject(
        id=row.id,
        endpoint=row.endpoint,
        input_file_id=row.input_file_id,
        output_file_id=row.output_file_id,
        error_file_id=row.error_file_id,
        completion_window=row.completion_window,
        status=row.status,
        request_counts=BatchRequestCounts(
            total=row.total_requests,
            completed=row.completed_requests,
            failed=row.failed_requests,
        ),
        created_at=_ts(row.created_at) or int(time.time()),
        in_progress_at=_ts(row.in_progress_at),
        finalizing_at=_ts(row.finalizing_at),
        completed_at=_ts(row.completed_at),
        failed_at=_ts(row.failed_at),
        cancelled_at=_ts(row.cancelled_at),
        expires_at=_ts(row.expires_at),
        metadata=row.metadata_,
    )


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _set_batch_status(batch_id: str, status: str, **extra_fields) -> None:
    async with async_session() as db:
        values: dict = {"status": status}
        now = now_ist()
        if status == "in_progress":
            values["in_progress_at"] = now
        elif status == "finalizing":
            values["finalizing_at"] = now
        elif status == "completed":
            values["completed_at"] = now
        elif status == "failed":
            values["failed_at"] = now
        elif status == "cancelled":
            values["cancelled_at"] = now
        values.update(extra_fields)
        await db.execute(update(Batch).where(Batch.id == batch_id).values(**values))
        await db.commit()


async def _increment_counts(batch_id: str, completed: int = 0, failed: int = 0) -> None:
    async with async_session() as db:
        row = (await db.execute(select(Batch).where(Batch.id == batch_id))).scalar_one_or_none()
        if row:
            row.completed_requests += completed
            row.failed_requests += failed
            await db.commit()


async def _process_one(request_line: dict, api_key: str) -> dict:
    """Process a single batch request line. Returns an output line dict."""
    custom_id = request_line.get("custom_id", "unknown")
    body = request_line.get("body", {})

    try:
        from a1.proxy.core_pipeline import CorePipelineInput, core_pipeline
        from a1.proxy.request_models import ChatCompletionRequest

        req = ChatCompletionRequest(**body)
        inp = CorePipelineInput(
            request_id=f"batch-{uuid.uuid4().hex[:8]}",
            source="batch",
            messages=list(req.messages),
            raw_user_input=next((m.content for m in reversed(req.messages) if m.role == "user"), "")
            or "",
            model=req.model,
            max_tokens=req.max_tokens or 1000,
            temperature=req.temperature,
            stream=False,
            tools=req.tools,
        )
        result = await core_pipeline.execute(inp)

        if result.error:
            return {
                "id": f"breq-{uuid.uuid4().hex[:12]}",
                "custom_id": custom_id,
                "response": None,
                "error": {"code": "provider_error", "message": result.error},
            }

        response_body = {
            "id": f"chatcmpl-{uuid.uuid4().hex[:12]}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": result.model_name or req.model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": result.assistant_text or ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": result.prompt_tokens,
                "completion_tokens": result.completion_tokens,
                "total_tokens": result.total_tokens,
            },
        }
        return {
            "id": f"breq-{uuid.uuid4().hex[:12]}",
            "custom_id": custom_id,
            "response": {"status_code": 200, "body": response_body},
            "error": None,
        }

    except Exception as e:
        log.warning(f"[batch] request custom_id={custom_id} failed: {e}")
        return {
            "id": f"breq-{uuid.uuid4().hex[:12]}",
            "custom_id": custom_id,
            "response": None,
            "error": {"code": "internal_error", "message": str(e)},
        }


async def _run_batch(batch_id: str, input_path: str, api_key: str) -> None:
    """Background worker: read input JSONL, process all requests, write output JSONL."""
    try:
        # Parse input
        lines = Path(input_path).read_text(encoding="utf-8").strip().splitlines()
        requests: list[dict] = []
        for line in lines:
            line = line.strip()
            if line:
                try:
                    requests.append(json.loads(line))
                except json.JSONDecodeError:
                    log.warning(f"[batch] {batch_id} skipping malformed line: {line[:80]}")

        total = len(requests)
        if total == 0:
            await _set_batch_status(
                batch_id, "failed", errors={"message": "Input file has no valid JSONL lines"}
            )
            return

        if total > _MAX_REQUESTS:
            await _set_batch_status(
                batch_id,
                "failed",
                errors={"message": f"Too many requests: {total} > {_MAX_REQUESTS}"},
            )
            return

        await _set_batch_status(batch_id, "in_progress", total_requests=total)
        log.info(f"[batch] {batch_id} started — {total} requests, parallelism={_MAX_PARALLEL}")

        # Process with semaphore-controlled concurrency
        sem = asyncio.Semaphore(_MAX_PARALLEL)
        results: list[dict] = [None] * total  # type: ignore[list-item]

        async def _do(idx: int, req: dict) -> None:
            async with sem:
                results[idx] = await _process_one(req, api_key)
                is_error = results[idx].get("error") is not None
                await _increment_counts(
                    batch_id, completed=0 if is_error else 1, failed=1 if is_error else 0
                )

        await asyncio.gather(*[_do(i, r) for i, r in enumerate(requests)])

        # Write output file
        await _set_batch_status(batch_id, "finalizing")

        output_jsonl = "\n".join(json.dumps(r) for r in results if r is not None)
        output_file_id = f"file-{uuid.uuid4().hex}"
        upload_root = Path(settings.upload_dir)
        out_dir = upload_root / output_file_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "batch_output.jsonl"
        out_path.write_text(output_jsonl, encoding="utf-8")

        async with async_session() as db:
            async with db.begin():
                from a1.db.models import UploadedFile

                db.add(
                    UploadedFile(
                        id=output_file_id,
                        filename="batch_output.jsonl",
                        purpose="batch",
                        bytes_=len(output_jsonl.encode()),
                        mime_type="application/jsonl",
                        storage_path=str(out_path),
                    )
                )

        failed = sum(1 for r in results if r and r.get("error"))
        completed = total - failed
        await _set_batch_status(
            batch_id,
            "completed",
            output_file_id=output_file_id,
            completed_requests=completed,
            failed_requests=failed,
        )
        log.info(f"[batch] {batch_id} completed — {completed} ok, {failed} failed")

    except asyncio.CancelledError:
        await _set_batch_status(batch_id, "cancelled")
        log.info(f"[batch] {batch_id} cancelled")
    except Exception as e:
        log.error(f"[batch] {batch_id} worker error: {e}")
        await _set_batch_status(batch_id, "failed", errors={"message": str(e)})
    finally:
        _active_tasks.pop(batch_id, None)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/v1/batches", response_model=BatchObject)
async def create_batch(
    body: CreateBatchRequest,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    if body.endpoint not in _SUPPORTED_ENDPOINTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported endpoint '{body.endpoint}'. Supported: {sorted(_SUPPORTED_ENDPOINTS)}"
            ),
        )

    # Resolve input file
    async with async_session() as db:
        file_row = (
            await db.execute(select(UploadedFile).where(UploadedFile.id == body.input_file_id))
        ).scalar_one_or_none()

    if not file_row:
        raise HTTPException(status_code=404, detail=f"Input file '{body.input_file_id}' not found.")
    if not Path(file_row.storage_path).exists():
        raise HTTPException(status_code=404, detail="Input file content not found on disk.")

    batch_id = f"batch-{uuid.uuid4().hex[:16]}"
    now = now_ist()

    async with async_session() as db:
        async with db.begin():
            db.add(
                Batch(
                    id=batch_id,
                    input_file_id=body.input_file_id,
                    endpoint=body.endpoint,
                    completion_window=body.completion_window,
                    status="validating",
                    metadata_=body.metadata,
                    created_at=now,
                )
            )

    # Launch background worker
    task = asyncio.create_task(_run_batch(batch_id, file_row.storage_path, _api_key))
    _active_tasks[batch_id] = task

    log.info(f"[batch] created {batch_id} from file={body.input_file_id}")

    # Return initial object
    return BatchObject(
        id=batch_id,
        endpoint=body.endpoint,
        input_file_id=body.input_file_id,
        completion_window=body.completion_window,
        status="validating",
        request_counts=BatchRequestCounts(total=0, completed=0, failed=0),
        created_at=int(now.timestamp()),
        metadata=body.metadata,
    )


@router.get("/v1/batches", response_model=list[BatchObject])
async def list_batches(
    limit: int = 20,
    _api_key: str = Depends(verify_api_key),
) -> Any:
    async with async_session() as db:
        rows = (
            (
                await db.execute(
                    select(Batch).order_by(Batch.created_at.desc()).limit(min(limit, 100))
                )
            )
            .scalars()
            .all()
        )
    return [_to_batch_obj(r) for r in rows]


@router.get("/v1/batches/{batch_id}", response_model=BatchObject)
async def get_batch(batch_id: str, _api_key: str = Depends(verify_api_key)) -> Any:
    async with async_session() as db:
        row = (await db.execute(select(Batch).where(Batch.id == batch_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Batch '{batch_id}' not found.")
    return _to_batch_obj(row)


@router.post("/v1/batches/{batch_id}/cancel", response_model=BatchObject)
async def cancel_batch(batch_id: str, _api_key: str = Depends(verify_api_key)) -> Any:
    async with async_session() as db:
        row = (await db.execute(select(Batch).where(Batch.id == batch_id))).scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Batch '{batch_id}' not found.")
    if row.status in ("completed", "failed", "cancelled", "expired"):
        raise HTTPException(status_code=400, detail=f"Batch is already '{row.status}'.")

    task = _active_tasks.get(batch_id)
    if task and not task.done():
        task.cancel()

    await _set_batch_status(batch_id, "cancelled")

    async with async_session() as db:
        row = (await db.execute(select(Batch).where(Batch.id == batch_id))).scalar_one_or_none()
    return _to_batch_obj(row)
