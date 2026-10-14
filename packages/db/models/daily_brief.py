"""Daily brief model."""
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, Text, text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class DailyBrief(Base):
    __tablename__ = "daily_briefs"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    brief_date: Mapped[date] = mapped_column(Date, nullable=False)
    block_slug: Mapped[str] = mapped_column(String(64), nullable=False, server_default=text("'trading'"))
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    chain_ids: Mapped[list[str]] = mapped_column(
        ARRAY(UUID(as_uuid=False)), nullable=False, server_default=text("'{}'::uuid[]")
    )
    generated_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    model_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    prompt_version_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'draft'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    # S.7.6a — soft delete. NULL = visible; non-null = in trash.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
