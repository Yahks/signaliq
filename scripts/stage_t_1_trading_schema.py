"""
SignalIQ — Phase T.1: Trading block schema.

Creates:
  - trading_subblocks  (13 global rows, org-overridable)
  - trading_posts      (org-scoped, RLS-enforced)

Each subblock is self-describing:
  - style_guide      text    — LLM system-prompt hints (voice, tone, structure)
  - theme            jsonb   — UI colors, icon, layout hints
  - training_config  jsonb   — data sources, cadence, symbols, generation rules

Pre-flight asserts, applies migrations, verifies, prints ✅/❌.
Idempotent. Fails loudly.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATION_24 = ROOT / "alembic" / "versions" / "0024_trading_subblocks.py"
MIGRATION_25 = ROOT / "alembic" / "versions" / "0025_trading_posts.py"


# ─────────────────────────────────────────────────────────────────────
# Pre-flight
# ─────────────────────────────────────────────────────────────────────

def preflight() -> None:
    print("[0/5] Pre-flight checks")

    # Alembic head
    r = subprocess.run(
        ["alembic", "current"], cwd=ROOT, capture_output=True, text=True,
    )
    out = (r.stdout or "") + (r.stderr or "")
    if "0022 (head)" not in out:
        raise SystemExit(
            f"\n❌ Expected alembic head 0022, got:\n{out}\n"
            f"   Do not proceed. Resolve migrations first."
        )
    print("  ✓ alembic head = 0022")

    # Migrations don't already exist
    for m in (MIGRATION_24, MIGRATION_25):
        if m.exists():
            raise SystemExit(
                f"\n❌ {m.name} already exists.\n"
                f"   If it was applied, verify with: alembic current\n"
                f"   If it wasn't, delete it and re-run."
            )
    print("  ✓ 0024 + 0025 not yet written")

    # Tables don't already exist
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc",
         "SELECT tablename FROM pg_tables "
         "WHERE schemaname='public' AND tablename IN "
         "('trading_subblocks','trading_posts');"],
        capture_output=True, text=True,
    )
    existing = (r.stdout or "").strip()
    if existing:
        raise SystemExit(
            f"\n❌ Tables already exist: {existing}\n"
            f"   Do not proceed — resolve duplicates first."
        )
    print("  ✓ trading_subblocks / trading_posts do not exist")

    # blocks.trading must be present (seed is upsert, but warn if absent)
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-tAc",
         "SELECT id::text FROM blocks WHERE slug='trading' AND org_id IS NULL;"],
        capture_output=True, text=True,
    )
    trading_block_id = (r.stdout or "").strip()
    if not trading_block_id:
        print("  ⚠ blocks.trading not found — the seed will create it")
    else:
        print(f"  ✓ blocks.trading present (id={trading_block_id[:8]}…)")

    # FRED key present (informational)
    env = (ROOT / ".env").read_text() if (ROOT / ".env").exists() else ""
    if "FRED_API_KEY=" not in env or "FRED_API_KEY=\n" in env:
        print("  ⚠ FRED_API_KEY not detected in .env — T.2 FRED client will need it")
    else:
        print("  ✓ FRED_API_KEY present in .env")


# ─────────────────────────────────────────────────────────────────────
# Migration 0024 — trading_subblocks
# ─────────────────────────────────────────────────────────────────────

MIGRATION_24_SRC = '''"""Trading subblocks: 13 first-class content verticals under the trading block.

Each subblock is self-describing:
  - style_guide      text    — LLM system-prompt voice/tone/structure
  - theme            jsonb   — UI colors, icon, layout
  - training_config  jsonb   — data sources, cadence, symbols, generation rules

Revision ID: 0024
Revises: 0022
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0024"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# The 13 subblocks. Order here = default sort_order.
SUBBLOCKS = [
    {
        "slug": "market_analysis",
        "name": "Market Analysis",
        "description": "Daily read of index, breadth, and sector rotation with a narrative thesis.",
        "category": "market",
        "cadence": "hourly_market",
        "source_kind": "yahoo",
        "sort_order": 10,
        "theme": {"color": "#3b82f6", "icon": "LineChart", "layout": "dashboard"},
        "style_guide": (
            "Voice: sell-side strategist. Lead with the headline move, then breadth, "
            "then sector rotation, then one paragraph of 'what it means'. Never give "
            "buy/sell advice; frame as observation. Numbers must come from data."
        ),
        "training_config": {
            "data_sources": ["yahoo.quote", "yahoo.history"],
            "symbols": ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX"],
            "sectors": ["XLK", "XLF", "XLE", "XLV", "XLI", "XLP", "XLY", "XLU", "XLB", "XLRE", "XLC"],
            "window_days": 5,
        },
    },
    {
        "slug": "trading_psychology",
        "name": "Trading Psychology",
        "description": "Behavioural lessons applied to the current market regime.",
        "category": "education",
        "cadence": "daily",
        "source_kind": "llm_curated",
        "sort_order": 20,
        "theme": {"color": "#a855f7", "icon": "Brain", "layout": "article"},
        "style_guide": (
            "Voice: calm trading coach. Ground every lesson in a concrete market "
            "scenario (drawdown, FOMO, revenge trade, overconfidence). No promises, "
            "no 'you will make money'. Acknowledge uncertainty."
        ),
        "training_config": {
            "corpus": "psychology",
            "topic_pool": [
                "loss aversion", "confirmation bias", "revenge trading",
                "FOMO", "overconfidence after wins", "position sizing discipline",
                "journaling", "risk of ruin", "drawdown psychology",
            ],
            "words": 400,
        },
    },
    {
        "slug": "credit_risk",
        "name": "Credit & Risk Management",
        "description": "Rates, spreads, credit conditions, and portfolio risk framing.",
        "category": "macro",
        "cadence": "daily",
        "source_kind": "fred",
        "sort_order": 30,
        "theme": {"color": "#ef4444", "icon": "ShieldAlert", "layout": "dashboard"},
        "style_guide": (
            "Voice: risk officer. Lead with the current state of rates and spreads. "
            "Then what it implies for leverage and duration. All numbers from FRED; "
            "cite series IDs."
        ),
        "training_config": {
            "fred_series": ["DGS10", "DGS2", "T10Y2Y", "BAMLH0A0HYM2", "BAMLC0A0CM"],
            "narrative_topics": [
                "term structure", "credit spreads", "risk parity pressure",
                "duration risk", "yield curve inversion",
            ],
        },
    },
    {
        "slug": "investment_strategies",
        "name": "Investment Strategies",
        "description": "Named strategies explained with current-market applicability.",
        "category": "education",
        "cadence": "daily",
        "source_kind": "llm_curated",
        "sort_order": 40,
        "theme": {"color": "#10b981", "icon": "Target", "layout": "article"},
        "style_guide": (
            "Voice: patient allocator. Explain the strategy, when it works, when it "
            "breaks, and one concrete example. No backtested promises. Every strategy "
            "must include 'what could go wrong'."
        ),
        "training_config": {
            "corpus": "strategies",
            "strategy_pool": [
                "risk parity", "trend following", "carry", "value + momentum combo",
                "core-satellite", "barbell", "buy-and-hold vs tactical", "dollar-cost averaging",
                "all-weather", "60/40 critique",
            ],
            "words": 600,
        },
    },
    {
        "slug": "trading_styles",
        "name": "Trading Styles",
        "description": "Scalping, day, swing, position — compared on cost, edge, and fit.",
        "category": "education",
        "cadence": "daily",
        "source_kind": "llm_curated",
        "sort_order": 50,
        "theme": {"color": "#f59e0b", "icon": "Activity", "layout": "article"},
        "style_guide": (
            "Voice: practical educator. Compare styles honestly — costs, screen time, "
            "capital requirements, psychological load. No 'best' style; frame as fit-for-person."
        ),
        "training_config": {
            "corpus": "styles",
            "style_pool": [
                "scalping", "day trading", "swing trading", "position trading",
                "trend following", "mean reversion", "breakout", "event-driven",
                "arbitrage", "market making",
            ],
            "words": 500,
        },
    },
    {
        "slug": "fundamental_metrics",
        "name": "Fundamental Metrics",
        "description": "One company per post, decoded via filed metrics.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "edgar",
        "sort_order": 60,
        "theme": {"color": "#0ea5e9", "icon": "FileBarChart", "layout": "dashboard"},
        "style_guide": (
            "Voice: equity analyst. Use only SEC-filed numbers. Cite the filing type "
            "and date. Call out anything ambiguous as 'requires inspection'."
        ),
        "training_config": {
            "data_sources": ["edgar.companyfacts", "yahoo.quoteSummary"],
            "metrics": [
                "revenue growth", "gross margin", "operating margin",
                "free cash flow", "net debt / EBITDA", "ROIC",
            ],
            "universe": "sp500_top_50_by_cap",
        },
    },
    {
        "slug": "class_room",
        "name": "Class Room",
        "description": "Curriculum-style lesson per post, one concept at a time.",
        "category": "education",
        "cadence": "daily",
        "source_kind": "llm_curated",
        "sort_order": 70,
        "theme": {"color": "#8b5cf6", "icon": "GraduationCap", "layout": "article"},
        "style_guide": (
            "Voice: teacher. One concept, defined, then illustrated, then a short "
            "exercise. Assume the reader is a motivated beginner. Never condescending."
        ),
        "training_config": {
            "corpus": "curriculum",
            "curriculum": [
                "order types", "bid-ask spread", "slippage", "P/E vs EV/EBITDA",
                "yield curve basics", "correlation vs causation", "beta and alpha",
                "volatility vs risk", "margin mechanics", "options basics",
            ],
            "words": 500,
        },
    },
    {
        "slug": "economic_events",
        "name": "Economic Events",
        "description": "What's on the calendar, why it matters, what to watch.",
        "category": "macro",
        "cadence": "event_driven",
        "source_kind": "fred",
        "sort_order": 80,
        "theme": {"color": "#eab308", "icon": "CalendarClock", "layout": "dashboard"},
        "style_guide": (
            "Voice: macro strategist. Lead with the event, then the consensus (if "
            "available), then what each surprise direction implies. No predictions; "
            "frame as 'if X then Y'."
        ),
        "training_config": {
            "fred_series": [
                "CPIAUCSL", "UNRATE", "PAYEMS", "GDP", "FEDFUNDS",
                "PCEPI", "PPIACO", "INDPRO",
            ],
            "horizon_days": 14,
        },
    },
    {
        "slug": "trading_tools",
        "name": "Trading Tools and Platform",
        "description": "One platform or tool per post, honest pros/cons.",
        "category": "tools",
        "cadence": "daily",
        "source_kind": "llm_curated",
        "sort_order": 90,
        "theme": {"color": "#06b6d4", "icon": "Wrench", "layout": "article"},
        "style_guide": (
            "Voice: practical reviewer. No affiliate tone. Name one strength and one "
            "genuine limitation. Disclose that this is informational, not endorsement."
        ),
        "training_config": {
            "corpus": "tools",
            "tool_pool": [
                "TradingView", "Interactive Brokers", "Thinkorswim", "NinjaTrader",
                "MetaTrader 5", "Koyfin", "Finviz", "StockCharts", "Barchart",
                "Seeking Alpha", "FRED", "Koyfin screener",
            ],
            "words": 450,
        },
    },
    {
        "slug": "market_condition",
        "name": "Market Condition",
        "description": "Regime read — trending, chopping, risk-on, risk-off.",
        "category": "market",
        "cadence": "hourly_market",
        "source_kind": "yahoo",
        "sort_order": 100,
        "theme": {"color": "#14b8a6", "icon": "Gauge", "layout": "dashboard"},
        "style_guide": (
            "Voice: tactician. Classify the regime, cite the evidence (VIX, breadth, "
            "credit, dollar), and one paragraph on what changes the classification."
        ),
        "training_config": {
            "data_sources": ["yahoo.quote", "yahoo.history"],
            "signals": ["^VIX", "^VIX3M", "^TNX", "DX-Y.NYB", "^GSPC", "HYG", "LQD"],
            "window_days": 20,
        },
    },
    {
        "slug": "technical_indicators",
        "name": "Technical Indicator",
        "description": "Indicator of the day computed on a tracked symbol.",
        "category": "market",
        "cadence": "hourly_market",
        "source_kind": "computed",
        "sort_order": 110,
        "theme": {"color": "#6366f1", "icon": "TrendingUp", "layout": "dashboard"},
        "style_guide": (
            "Voice: technician. State the indicator, the value, the historical context, "
            "and one caveat. Never present a signal as a trade recommendation."
        ),
        "training_config": {
            "data_sources": ["yahoo.history"],
            "indicators": ["RSI(14)", "MACD(12,26,9)", "ATR(14)", "Bollinger(20,2)", "SMA(50/200)"],
            "symbols": ["SPY", "QQQ", "IWM", "TLT", "GLD"],
        },
    },
    {
        "slug": "market_cap",
        "name": "Market Cap",
        "description": "Who moved up and down the cap table, and why.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "yahoo",
        "sort_order": 120,
        "theme": {"color": "#ec4899", "icon": "BarChart3", "layout": "dashboard"},
        "style_guide": (
            "Voice: market watcher. List the top movers by absolute cap change. "
            "One sentence of 'why' per name (from real events if available). "
            "No speculation beyond sourced news."
        ),
        "training_config": {
            "data_sources": ["yahoo.quote"],
            "universe": "sp500_top_100_by_cap",
            "top_n": 10,
        },
    },
    {
        "slug": "asset_classes",
        "name": "Asset Classes",
        "description": "Cross-asset performance — equities, bonds, commodities, FX, crypto.",
        "category": "market",
        "cadence": "daily",
        "source_kind": "yahoo",
        "sort_order": 130,
        "theme": {"color": "#84cc16", "icon": "Layers", "layout": "dashboard"},
        "style_guide": (
            "Voice: macro allocator. One table of YTD and 1-week returns per class, "
            "then one paragraph on the cross-asset story of the day."
        ),
        "training_config": {
            "data_sources": ["yahoo.quote", "yahoo.history"],
            "assets": {
                "equities": ["^GSPC", "^IXIC", "^RUT"],
                "bonds": ["^TNX", "TLT", "IEF", "SHY"],
                "commodities": ["GC=F", "CL=F", "SI=F", "NG=F"],
                "fx": ["DX-Y.NYB", "EURUSD=X", "JPY=X"],
                "crypto": ["BTC-USD", "ETH-USD"],
            },
        },
    },
]


def upgrade() -> None:
    op.create_table(
        "trading_subblocks",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=True),
        sa.Column("block_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("blocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("slug", sa.String(64), nullable=False),
        sa.Column("name", sa.String(128), nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("category", sa.String(32), nullable=True),
        sa.Column("cadence", sa.String(32), nullable=False, server_default=sa.text("'daily'")),
        sa.Column("source_kind", sa.String(32), nullable=True),
        sa.Column("theme", postgresql.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("style_guide", sa.Text, nullable=True),
        sa.Column("training_config", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default=sa.text("true")),
        sa.Column("sort_order", sa.Integer, nullable=False, server_default=sa.text("100")),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_trading_subblocks_block",
                    "trading_subblocks", ["block_id", "enabled", "sort_order"])
    op.create_index("ux_trading_subblocks_system_slug",
                    "trading_subblocks", ["slug"], unique=True,
                    postgresql_where=sa.text("org_id IS NULL"))
    op.create_index("ux_trading_subblocks_org_slug",
                    "trading_subblocks", ["org_id", "slug"], unique=True,
                    postgresql_where=sa.text("org_id IS NOT NULL"))

    # RLS + grant
    op.execute("ALTER TABLE trading_subblocks ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trading_subblocks FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trading_subblocks ON trading_subblocks
            USING (
                org_id IS NULL
                OR org_id = current_setting('app.current_org_id', true)::uuid
            )
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trading_subblocks TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trading_subblocks ON trading_subblocks")
    op.drop_index("ux_trading_subblocks_org_slug", table_name="trading_subblocks")
    op.drop_index("ux_trading_subblocks_system_slug", table_name="trading_subblocks")
    op.drop_index("ix_trading_subblocks_block", table_name="trading_subblocks")
    op.drop_table("trading_subblocks")
'''


# ─────────────────────────────────────────────────────────────────────
# Migration 0025 — trading_posts
# ─────────────────────────────────────────────────────────────────────

MIGRATION_25_SRC = '''"""Trading posts: generated content per subblock per org.

Revision ID: 0025
Revises: 0024
Create Date: 2026-09-19
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "trading_posts",
        sa.Column("id", postgresql.UUID(as_uuid=False),
                  server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("org_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("orgs.id", ondelete="CASCADE"), nullable=False),
        sa.Column("subblock_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("trading_subblocks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("title", sa.String(500), nullable=False),
        sa.Column("body", sa.Text, nullable=False),
        sa.Column("format", sa.String(32), nullable=False, server_default=sa.text("'long_form'")),
        sa.Column("source_kind", sa.String(32), nullable=False),
        sa.Column("source_payload", postgresql.JSONB, nullable=False,
                  server_default=sa.text("'{}'::jsonb")),
        sa.Column("source_urls", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("symbols", postgresql.ARRAY(sa.Text), nullable=False,
                  server_default=sa.text("'{}'::text[]")),
        sa.Column("model_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("ai_models.id"), nullable=True),
        sa.Column("prompt_version_id", postgresql.UUID(as_uuid=False),
                  sa.ForeignKey("prompt_versions.id"), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("word_count", sa.Integer, nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.text("now()"), nullable=False),
    )
    op.create_index(
        "ix_trading_posts_org_subblock_time",
        "trading_posts", ["org_id", "subblock_id", "published_at"],
        postgresql_using="btree",
    )
    op.create_index("ix_trading_posts_source_kind", "trading_posts", ["source_kind"])

    op.execute("ALTER TABLE trading_posts ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE trading_posts FORCE ROW LEVEL SECURITY")
    op.execute("""
        CREATE POLICY tenant_isolation_trading_posts ON trading_posts
            USING (org_id = current_setting('app.current_org_id', true)::uuid)
    """)
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON trading_posts TO signaliq_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS tenant_isolation_trading_posts ON trading_posts")
    op.drop_index("ix_trading_posts_source_kind", table_name="trading_posts")
    op.drop_index("ix_trading_posts_org_subblock_time", table_name="trading_posts")
    op.drop_table("trading_posts")
'''


# ─────────────────────────────────────────────────────────────────────
# Seed SQL (as Python dicts → JSON, inserted via psycopg)
# ─────────────────────────────────────────────────────────────────────

def _seed_sql(subblocks: list[dict]) -> str:
    """Build a single INSERT ... ON CONFLICT for the 13 subblocks.

    block_id resolves via subquery on blocks.slug='trading'.
    org_id is NULL (global default).
    Idempotent via ON CONFLICT on (slug) WHERE org_id IS NULL.
    """
    # We insert one row at a time so the ON CONFLICT partial index works cleanly.
    parts = []
    for sb in subblocks:
        theme = json.dumps(sb["theme"]).replace("'", "''")
        style = sb["style_guide"].replace("'", "''")
        training = json.dumps(sb["training_config"]).replace("'", "''")
        desc = (sb.get("description") or "").replace("'", "''")
        parts.append(f"""
            INSERT INTO trading_subblocks
                (org_id, block_id, slug, name, description, category, cadence,
                 source_kind, theme, style_guide, training_config, enabled, sort_order)
            VALUES (
                NULL,
                (SELECT id FROM blocks WHERE slug = 'trading' AND org_id IS NULL LIMIT 1),
                '{sb["slug"]}', '{sb["name"]}', '{desc}',
                '{sb["category"]}', '{sb["cadence"]}', '{sb["source_kind"]}',
                '{theme}'::jsonb, '{style}', '{training}'::jsonb,
                true, {sb["sort_order"]}
            )
            ON CONFLICT (slug) WHERE org_id IS NULL DO UPDATE
                SET name = EXCLUDED.name,
                    description = EXCLUDED.description,
                    category = EXCLUDED.category,
                    cadence = EXCLUDED.cadence,
                    source_kind = EXCLUDED.source_kind,
                    theme = EXCLUDED.theme,
                    style_guide = EXCLUDED.style_guide,
                    training_config = EXCLUDED.training_config,
                    sort_order = EXCLUDED.sort_order,
                    updated_at = now();
        """)
    return "\n".join(parts)


def write_migrations() -> None:
    print("\n[1/5] Write migrations")
    MIGRATION_24.write_text(MIGRATION_24_SRC)
    print(f"  ✓ wrote {MIGRATION_24.name}")
    MIGRATION_25.write_text(MIGRATION_25_SRC)
    print(f"  ✓ wrote {MIGRATION_25.name}")


def apply_migrations() -> None:
    print("\n[2/5] alembic upgrade head")
    r = subprocess.run(
        ["alembic", "upgrade", "head"], cwd=ROOT,
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(f"\n❌ alembic upgrade head failed:\n{r.stderr}")
    print("  ✓ migrated")
    for line in (r.stderr or "").splitlines():
        if "Running upgrade" in line:
            print(f"      {line.strip()}")


def seed_subblocks() -> None:
    print("\n[3/5] Seed 13 subblocks (idempotent upsert)")
    # Import the SUBBLOCKS list directly from migration 24
    sys.path.insert(0, str(ROOT))
    import importlib.util
    spec = importlib.util.spec_from_file_location("m24", MIGRATION_24)
    m24 = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m24)  # type: ignore[union-attr]

    sql = _seed_sql(m24.SUBBLOCKS)

    # Write the SQL to a temp file and feed it to psql (avoids quoting hell)
    tmp = Path("/tmp/seed_trading_subblocks.sql")
    tmp.write_text(sql)

    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-v", "ON_ERROR_STOP=1"],
        input=sql, capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise SystemExit(
            f"\n❌ seed failed:\n{r.stderr}\n\nSQL was written to {tmp}"
        )
    print(f"  ✓ seeded {len(m24.SUBBLOCKS)} subblocks")


def verify() -> None:
    print("\n[4/5] Verify")

    checks: list[tuple[str, str, callable]] = []  # type: ignore[type-arg]

    def run_sql(sql: str) -> str:
        r = subprocess.run(
            ["docker", "exec", "-i", "signaliq-postgres-1",
             "psql", "-U", "signaliq", "-d", "signaliq", "-tAc", sql],
            capture_output=True, text=True,
        )
        return (r.stdout or "").strip()

    # 1. tables exist
    tables = run_sql(
        "SELECT string_agg(tablename, ',' ORDER BY tablename) "
        "FROM pg_tables WHERE schemaname='public' "
        "AND tablename IN ('trading_posts','trading_subblocks');"
    )
    ok_tables = tables == "trading_posts,trading_subblocks"
    print(f"  {'✓' if ok_tables else '❌'} tables present: {tables}")
    if not ok_tables:
        raise SystemExit("  ❌ expected both tables present")

    # 2. RLS enabled on both
    rls = run_sql(
        "SELECT string_agg(relname, ',' ORDER BY relname) "
        "FROM pg_class WHERE relname IN ('trading_posts','trading_subblocks') "
        "AND relrowsecurity = true AND relforcerowsecurity = true;"
    )
    ok_rls = rls == "trading_posts,trading_subblocks"
    print(f"  {'✓' if ok_rls else '❌'} RLS enabled+forced: {rls}")
    if not ok_rls:
        raise SystemExit("  ❌ RLS not enabled on both tables")

    # 3. policies exist
    policies = run_sql(
        "SELECT string_agg(policyname, ',' ORDER BY policyname) "
        "FROM pg_policies WHERE tablename IN "
        "('trading_posts','trading_subblocks');"
    )
    ok_pol = "tenant_isolation_trading_posts" in policies and \
             "tenant_isolation_trading_subblocks" in policies
    print(f"  {'✓' if ok_pol else '❌'} policies: {policies}")
    if not ok_pol:
        raise SystemExit("  ❌ policies missing")

    # 4. exactly 13 subblocks, all global, all pointing at trading block
    count = run_sql("SELECT count(*) FROM trading_subblocks WHERE org_id IS NULL;")
    ok_count = count == "13"
    print(f"  {'✓' if ok_count else '❌'} 13 global subblocks: {count}")
    if not ok_count:
        raise SystemExit(f"  ❌ expected 13 subblocks, got {count}")

    wrong_block = run_sql("""
        SELECT count(*) FROM trading_subblocks ts
        JOIN blocks b ON b.id = ts.block_id
        WHERE b.slug <> 'trading';
    """)
    ok_block = wrong_block == "0"
    print(f"  {'✓' if ok_block else '❌'} all subblocks under trading block: {wrong_block} wrong")
    if not ok_block:
        raise SystemExit("  ❌ some subblocks point to wrong block")

    # 5. all 13 have non-empty style_guide + training_config + theme
    thin = run_sql("""
        SELECT count(*) FROM trading_subblocks
        WHERE org_id IS NULL
          AND (style_guide IS NULL OR length(style_guide) < 20
               OR training_config = '{}'::jsonb
               OR theme = '{}'::jsonb);
    """)
    ok_thin = thin == "0"
    print(f"  {'✓' if ok_thin else '❌'} self-describing: {thin} thin rows")
    if not ok_thin:
        raise SystemExit("  ❌ some subblocks missing style_guide/theme/training_config")

    # 6. grants present
    grants = run_sql("""
        SELECT count(*) FROM information_schema.role_table_grants
        WHERE grantee='signaliq_app'
          AND table_name IN ('trading_subblocks','trading_posts');
    """)
    ok_grants = int(grants or "0") >= 8  # 4 privileges × 2 tables
    print(f"  {'✓' if ok_grants else '❌'} signaliq_app grants: {grants}")
    if not ok_grants:
        raise SystemExit("  ❌ grants missing")


def sample() -> None:
    print("\n[5/5] Sample — first 3 subblocks")
    r = subprocess.run(
        ["docker", "exec", "-i", "signaliq-postgres-1",
         "psql", "-U", "signaliq", "-d", "signaliq", "-c",
         "SELECT sort_order, slug, category, cadence, source_kind "
         "FROM trading_subblocks WHERE org_id IS NULL "
         "ORDER BY sort_order LIMIT 3;"],
        capture_output=True, text=True,
    )
    print(r.stdout)


def main() -> None:
    print("=" * 70)
    print("SignalIQ — Phase T.1: Trading block schema")
    print("=" * 70)
    preflight()
    write_migrations()
    apply_migrations()
    seed_subblocks()
    verify()
    sample()
    print("=" * 70)
    print("✅ T.1 complete — trading_subblocks + trading_posts live")
    print("   Next: T.2 — free API clients (yahoo, fred, edgar, stooq)")
    print("=" * 70)


if __name__ == "__main__":
    main()
