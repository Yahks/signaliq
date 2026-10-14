"""Trader process — the trader's document, one per org.
And trader_process_drafts — one draft document per model run.
"""
from datetime import datetime

from sqlalchemy import DateTime, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from packages.db.base import Base


class TraderProcess(Base):
    __tablename__ = "trader_process"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    daily_scan: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    entry_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    position_sizing: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    exit_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    holding_period: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    research_to_rules: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    chart_overlay: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    no_backtest_rule: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    multi_strategy_blend: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TraderProcessDraft(Base):
    __tablename__ = "trader_process_drafts"

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    org_id: Mapped[str] = mapped_column(UUID(as_uuid=False), nullable=False)
    daily_scan: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    entry_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    position_sizing: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    exit_logic: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    holding_period: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    research_to_rules: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    chart_overlay: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    no_backtest_rule: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    multi_strategy_blend: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
