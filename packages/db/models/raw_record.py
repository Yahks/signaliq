"""Raw record model. Immutable original."""
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, String, Text, text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class RawRecord(Base):
    __tablename__ = "raw_records"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=sql_text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    source_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    ingestion_job_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    source_record_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    author_identifier: Mapped[str | None] = mapped_column(Text, nullable=True)
    original_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    language: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # maps SQLAlchemy attribute `metadata_` to column name `metadata`
    metadata_: Mapped[dict[str, Any]] = mapped_column(
        "metadata", JSONB, nullable=False, server_default=sql_text("'{}'::jsonb")
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sql_text("now()")
    )
