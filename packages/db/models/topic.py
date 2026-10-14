"""Topic and membership models."""
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class Topic(Base):
    __tablename__ = "topics"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_topic_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    is_hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    is_curated: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TopicMembership(Base):
    __tablename__ = "topic_memberships"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, server_default=text("gen_random_uuid()")
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    topic_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    normalized_record_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    similarity: Mapped[float | None] = mapped_column(Float, nullable=True)
    assigned_by: Mapped[str] = mapped_column(String(16), nullable=False, server_default=text("'llm'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
