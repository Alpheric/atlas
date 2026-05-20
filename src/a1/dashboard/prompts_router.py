"""Prompt versioning API (Phase 2.1) — list / create / activate prompt versions.

Prompts (the self-critique template, system-prompt suffixes, etc.) are stored
in the `prompt_versions` table. The pipeline reads the active version via
a1.common.prompt_registry.get_prompt, falling back to code defaults.
"""

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from a1.common.logging import get_logger
from a1.common.prompt_registry import invalidate
from a1.db.models import PromptVersion
from a1.dependencies import get_db

log = get_logger("dashboard.prompts")
router = APIRouter(prefix="/prompts", tags=["prompts"])


class PromptCreate(BaseModel):
    name: str
    content: str
    model: str | None = None
    description: str | None = None
    activate: bool = False  # if true, deactivate siblings and activate this one
    created_by: str | None = None


def _serialize(p: PromptVersion) -> dict:
    return {
        "id": str(p.id),
        "name": p.name,
        "version": p.version,
        "content": p.content,
        "model": p.model,
        "description": p.description,
        "is_active": p.is_active,
        "created_by": p.created_by,
        "created_at": p.created_at.isoformat() if p.created_at else None,
        "updated_at": p.updated_at.isoformat() if p.updated_at else None,
    }


@router.get("")
async def list_prompts(db: AsyncSession = Depends(get_db)):
    """List prompt names with version counts and the active version number."""
    rows = (
        await db.execute(
            select(
                PromptVersion.name,
                func.count().label("versions"),
                func.max(PromptVersion.updated_at).label("updated_at"),
            ).group_by(PromptVersion.name)
        )
    ).all()

    out = []
    for name, versions, updated_at in rows:
        active = (
            await db.execute(
                select(PromptVersion.version).where(
                    PromptVersion.name == name, PromptVersion.is_active.is_(True)
                )
            )
        ).scalars().first()
        out.append(
            {
                "name": name,
                "versions": int(versions),
                "active_version": active,
                "updated_at": updated_at.isoformat() if updated_at else None,
            }
        )
    return {"data": out, "total": len(out)}


@router.get("/{name}")
async def get_prompt_versions(name: str, db: AsyncSession = Depends(get_db)):
    """All versions of a named prompt, newest first."""
    rows = (
        await db.execute(
            select(PromptVersion)
            .where(PromptVersion.name == name)
            .order_by(PromptVersion.version.desc())
        )
    ).scalars().all()
    if not rows:
        raise HTTPException(404, f"No prompt named '{name}'")
    return {"name": name, "data": [_serialize(p) for p in rows]}


@router.post("")
async def create_prompt(body: PromptCreate, db: AsyncSession = Depends(get_db)):
    """Create a new version of a prompt (auto-incremented). Optionally activate it."""
    # Next version number for this name
    current_max = (
        await db.execute(
            select(func.max(PromptVersion.version)).where(PromptVersion.name == body.name)
        )
    ).scalar()
    next_version = (current_max or 0) + 1

    if body.activate:
        # Deactivate existing active versions of this name
        await db.execute(
            update(PromptVersion)
            .where(PromptVersion.name == body.name, PromptVersion.is_active.is_(True))
            .values(is_active=False)
        )

    pv = PromptVersion(
        id=uuid.uuid4(),
        name=body.name,
        version=next_version,
        content=body.content,
        model=body.model,
        description=body.description,
        is_active=body.activate,
        created_by=body.created_by,
    )
    db.add(pv)
    await db.commit()
    await db.refresh(pv)
    invalidate(body.name)
    return _serialize(pv)


@router.post("/{name}/activate/{version}")
async def activate_version(name: str, version: int, db: AsyncSession = Depends(get_db)):
    """Make a specific version the active one (deactivates the rest)."""
    target = (
        await db.execute(
            select(PromptVersion).where(
                PromptVersion.name == name, PromptVersion.version == version
            )
        )
    ).scalars().first()
    if not target:
        raise HTTPException(404, f"No version {version} for prompt '{name}'")

    await db.execute(
        update(PromptVersion)
        .where(PromptVersion.name == name)
        .values(is_active=False)
    )
    target.is_active = True
    await db.commit()
    invalidate(name)
    return _serialize(target)
