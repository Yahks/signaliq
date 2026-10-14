"""Studio chat message model.

Studio is the on-demand chat interface inside the trading block.
A user writes a free-form prompt; the model responds with an
article (saved as a trading_post) or a quick answer (message only).

Every user prompt and every assistant response is one row here.
Assistant rows that produced an article carry trading_post_id.
"""
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base, TimestampMixin


class StudioMessage(Base, TimestampMixin):
    __tablename__ = "studio_messages"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("orgs.id", ondelete="CASCADE"),
        nullable=False,
    )
    # S.8.9.7 — nullable. NULL means a system-generated message
    # (currently: movers subblock auto-articles). The chat endpoint
    # always sets it; the scanner passes None.
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    prompt_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # 'retail' | 'quant' | None. The audience track for this message.
    # NULL means the user did not choose a track.
    track: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # trading_posts is a raw-SQL table with no ORM model (see the
    # trading subblocks). The DB-level FK exists (created by migration
    # 0050); declaring it here would require an ORM model for
    # trading_posts, which the rest of the codebase deliberately
    # doesn't have. The constraint is enforced by Postgres regardless.
    trading_post_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False),
        nullable=True,
    )
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # S.8.11.1 — optional user-uploaded chart image. Path under
    # /static/uploads/. NULL means no image.
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
