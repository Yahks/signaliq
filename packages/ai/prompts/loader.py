"""Prompt loader with block-scoped overrides.

Prompt name lookup order:
    1. {name}.{block_slug}      (block-specific override)
    2. {name}                    (default)

Cached per (name, block_slug). Cache invalidated on process restart.
"""
from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text as sql_text
from sqlalchemy.orm import Session, sessionmaker

from packages.core.config import get_settings


def _sync_session() -> Session:
    settings = get_settings()
    engine = create_engine(settings.database_url_sync, pool_pre_ping=True)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _resolve_name(name: str, block_slug: str | None) -> str:
    """Pick the block-specific name if it exists, else the base name."""
    if not block_slug:
        return name
    session = _sync_session()
    try:
        # Check for block-specific active version
        row = session.execute(
            sql_text("""
                SELECT 1 FROM prompt_versions
                WHERE name = :blocked AND is_active = true
                LIMIT 1
            """),
            {"blocked": f"{name}.{block_slug}"},
        ).first()
        if row:
            return f"{name}.{block_slug}"
        return name
    finally:
        session.close()


@lru_cache(maxsize=256)
def _load_prompt(name: str) -> tuple[str, str]:
    """Load an active prompt by exact name. Returns (template, id)."""
    session = _sync_session()
    try:
        row = session.execute(
            sql_text("""
                SELECT id::text, template
                FROM prompt_versions
                WHERE name = :name AND is_active = true
                LIMIT 1
            """),
            {"name": name},
        ).first()
        if not row:
            raise LookupError(f"No active prompt named: {name}")
        return row[1], row[0]
    finally:
        session.close()


def get_prompt(name: str, *, block_slug: str | None = None) -> tuple[str, str]:
    """Return (template_text, prompt_version_id).

    Prefers a block-specific prompt if one exists.
    """
    resolved = _resolve_name(name, block_slug)
    return _load_prompt(resolved)


def render(name: str, *, block_slug: str | None = None, **kwargs: object) -> str:
    """Load a prompt template and substitute {{var}} placeholders."""
    template, _ = get_prompt(name, block_slug=block_slug)
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out


def render_with_version(
    name: str, *, block_slug: str | None = None, **kwargs: object
) -> tuple[str, str]:
    """Like render(), but also returns the prompt_version_id.

    Callers using this should pass the id to complete(prompt_version_id=_pv)
    so ai_calls.prompt_version_id gets populated.
    """
    template, version_id = get_prompt(name, block_slug=block_slug)
    out = template
    for key, value in kwargs.items():
        out = out.replace("{{" + key + "}}", str(value))
    return out, version_id


def clear_cache() -> None:
    """Clear all cached prompts (call after seeding)."""
    _load_prompt.cache_clear()
