"""Load block config + subtopics from the DB. Cached."""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from packages.core.config import get_settings
from packages.core.error import NotFoundError


@dataclass
class BlockConfig:
    id: str
    slug: str
    name: str
    description: str | None
    style_guide: str | None
    example_output: str | None
    config: dict
    subtopics: list[dict]
    is_system: bool

    def link_types(self) -> list[str]:
        return self.config.get("link_types") or []

    def event_types(self) -> list[str]:
        return self.config.get("event_types") or []

    def article_structure(self) -> list[dict]:
        return self.config.get("article_structure") or []

    def target_words(self) -> int:
        return int(self.config.get("default_target_words", 1200))

    def tone(self) -> str:
        return str(self.config.get("default_tone", "neutral"))

    def subtopic(self, slug: str) -> dict | None:
        for st in self.subtopics:
            if st.get("slug") == slug:
                return st
        return None


def _sync_session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def load_block_by_slug(slug: str, *, org_id: str | None = None) -> BlockConfig:
    """Load a block by slug.

    Precedence: org-specific block first, then system block.
    """
    session = _sync_session()
    try:
        # Try org-specific first if org_id given
        if org_id:
            row = session.execute(
                sql_text("""
                    SELECT id::text, slug, name, description, style_guide,
                           example_output, config, subtopics, is_system
                    FROM blocks
                    WHERE slug = :slug AND org_id = :org_id AND is_active = true
                    LIMIT 1
                """),
                {"slug": slug, "org_id": org_id},
            ).first()
            if row:
                return _row_to_config(row)

        # Fall back to system block
        row = session.execute(
            sql_text("""
                SELECT id::text, slug, name, description, style_guide,
                       example_output, config, subtopics, is_system
                FROM blocks
                WHERE slug = :slug AND org_id IS NULL AND is_active = true
                LIMIT 1
            """),
            {"slug": slug},
        ).first()
        if not row:
            raise NotFoundError(f"Block not found: {slug}")
        return _row_to_config(row)
    finally:
        session.close()


def list_blocks(org_id: str | None = None) -> list[BlockConfig]:
    """List all blocks visible to an org: system blocks + that org's custom blocks."""
    session = _sync_session()
    try:
        if org_id:
            rows = session.execute(
                sql_text("""
                    SELECT id::text, slug, name, description, style_guide,
                           example_output, config, subtopics, is_system
                    FROM blocks
                    WHERE is_active = true AND (org_id IS NULL OR org_id = :org_id)
                    ORDER BY is_system DESC, name
                """),
                {"org_id": org_id},
            ).all()
        else:
            rows = session.execute(
                sql_text("""
                    SELECT id::text, slug, name, description, style_guide,
                           example_output, config, subtopics, is_system
                    FROM blocks
                    WHERE is_active = true AND org_id IS NULL
                    ORDER BY name
                """),
            ).all()
        return [_row_to_config(r) for r in rows]
    finally:
        session.close()


def _row_to_config(row) -> BlockConfig:
    return BlockConfig(
        id=row[0],
        slug=row[1],
        name=row[2],
        description=row[3],
        style_guide=row[4],
        example_output=row[5],
        config=row[6] or {},
        subtopics=row[7] or [],
        is_system=row[8],
    )
