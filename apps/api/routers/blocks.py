"""Blocks: list and detail."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.deps import current_org_id
from packages.blocks.loader import load_block_by_slug, list_blocks

router = APIRouter(prefix="/api/blocks", tags=["blocks"])


class SubtopicSchema(BaseModel):
    slug: str
    name: str
    description: str | None = None


class BlockSchema(BaseModel):
    id: str
    slug: str
    name: str
    description: str | None
    style_guide: str | None
    config: dict
    subtopics: list[SubtopicSchema]
    is_system: bool


@router.get("", response_model=list[BlockSchema])
async def list_all_blocks(
    org_id: str = Depends(current_org_id),
) -> list[BlockSchema]:
    blocks = list_blocks(org_id=org_id)
    return [
        BlockSchema(
            id=b.id, slug=b.slug, name=b.name, description=b.description,
            style_guide=b.style_guide, config=b.config,
            subtopics=[SubtopicSchema(**st) for st in b.subtopics],
            is_system=b.is_system,
        )
        for b in blocks
    ]


@router.get("/{slug}", response_model=BlockSchema)
async def get_block(
    slug: str,
    org_id: str = Depends(current_org_id),
) -> BlockSchema:
    try:
        b = load_block_by_slug(slug, org_id=org_id)
    except Exception:
        raise HTTPException(404, f"Block not found: {slug}")
    return BlockSchema(
        id=b.id, slug=b.slug, name=b.name, description=b.description,
        style_guide=b.style_guide, config=b.config,
        subtopics=[SubtopicSchema(**st) for st in b.subtopics],
        is_system=b.is_system,
    )
