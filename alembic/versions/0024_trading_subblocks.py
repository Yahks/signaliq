"""Trading subblocks: 13 first-class content verticals under the trading block.

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
