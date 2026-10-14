"""Normalized record: cleaned, language-detected, deduped."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text, text as sql_text
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class NormalizedRecord(Base):
    __tablename__ = "normalized_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    raw_record_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    normalized_text: Mapped[str] = mapped_column(Text, nullable=False)
    translated_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    translation_lang: Mapped[str | None] = mapped_column(String(16), nullable=True)
    detected_language: Mapped[str] = mapped_column(String(16), nullable=False)
    language_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_duplicate: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    duplicate_of_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    is_spam: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    is_bot_suspected: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("false")
    )
    quality_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    tokens_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    urls: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    hashtags: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    mentions: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default=sql_text("'{}'::text[]")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
