"""Celery Beat schedule.

Runs the stock news fetcher on a cadence so chains keep receiving
updates without human intervention.

Design:
  - Default: every 30 minutes during market hours (UTC 13:00-21:00)
  - Every 2 hours outside market hours
  - SEC is hit less frequently (rates are slower)

The fetch task itself is idempotent: identical events deduplicate via
external_id, and reattach only fires on strong matches. Running more
often is safe, just uses more LLM tokens.
"""
from __future__ import annotations

from celery.schedules import crontab


def build_schedule() -> dict:
    """Return Celery Beat schedule for this deployment."""
    return {
        # Every 30 min during US market hours (13:30-20:00 UTC Mon-Fri)
        "stock-news-market-hours": {
            "task": "tasks.scheduled_stock_fetch",
            "schedule": crontab(minute="*/30", hour="13-20", day_of_week="1-5"),
        },
        # Every 2 hours outside market hours
        "stock-news-off-hours": {
            "task": "tasks.scheduled_stock_fetch",
            "schedule": crontab(minute="0", hour="0,2,4,6,8,10,22"),
        },
        # Daily briefs for all orgs — 12:00 UTC (US premarket)
        "daily-briefs": {
            "task": "tasks.generate_daily_briefs_all",
            "schedule": crontab(hour="12", minute="0"),
        },
        # Score predictions on finished matches (hourly)
        "score-predictions": {
            "task": "tasks.score_predictions",
            "schedule": crontab(minute="15"),
        },
        # Market data — daily, after US market close
        "trading-refresh-market-history": {
            "task": "tasks.trading_refresh_market_history",
            "schedule": crontab(hour="22", minute="30"),
            "options": {"queue": "ingestion"},
        },
        "trading-refresh-stock-metadata": {
            "task": "tasks.trading_refresh_stock_metadata",
            "schedule": crontab(hour="22", minute="45"),
            "options": {"queue": "ingestion"},
        },
        # Refresh watchlist market data every hour
        "warm-market-cache": {
            "task": "tasks.warm_market_cache",
            "schedule": crontab(minute="0"),
        },
        # Flush AI budget counters every 5 minutes
        "flush-ai-budget": {
            "task": "tasks.flush_ai_budget",
            "schedule": crontab(minute="*/5"),
        },
        # Daily re-cluster at 02:00 UTC
        "recluster-nightly": {
            "task": "tasks.recluster_all_orgs",
            "schedule": crontab(hour="2", minute="0"),
        },

        # ── Trading block (Phase T) ────────────────────────────────────
        # Hourly during US market hours (13:30-20:00 UTC) — Mon-Fri

        # Stock lists: every 30 min during the session
        "trading-stock-lists": {
            "task": "tasks.trading_generate_stock_lists",
            "schedule": crontab(minute="*/30", hour="13-20", day_of_week="1-5"),
        },
        # Market analysis: hourly on the hour
        "trading-market-analysis": {
            "task": "tasks.trading_generate_market_analysis",
            "schedule": crontab(minute="0", hour="14-20", day_of_week="1-5"),
        },
        # Market movers: scanner, hourly on the :20
        "trading-market-movers": {
            "task": "tasks.trading_generate_movers",
            "schedule": crontab(minute="20", hour="14-20", day_of_week="1-5"),
        },
        # Market condition: hourly on the half
        "trading-market-condition": {
            "task": "tasks.trading_generate_market_condition",
            "schedule": crontab(minute="30", hour="14-20", day_of_week="1-5"),
        },
        "trading-synthetic-trades": {
            "task": "tasks.trading_generate_synthetic_trades",
            "schedule": crontab(minute="*/2"),
        },

        # Daily pre-market signals at 11:00 UTC
        "trading-daily-market-cap": {
            "task": "tasks.trading_generate_market_cap",
            "schedule": crontab(hour="11", minute="0"),
        },
        "trading-daily-asset-classes": {
            "task": "tasks.trading_generate_asset_classes",
            "schedule": crontab(hour="11", minute="15"),
        },
        "trading-daily-market-signals": {
            "task": "tasks.trading_generate_market_signals",
            "schedule": crontab(hour="11", minute="30"),
        },
        "trading-daily-screener-picks": {
            "task": "tasks.trading_generate_screener_picks",
            "schedule": crontab(hour="11", minute="45"),
        },

        # Daily fundamentals + macro
        "trading-daily-fundamental": {
            "task": "tasks.trading_generate_fundamental_metrics",
            "schedule": crontab(hour="12", minute="0"),
        },
        "trading-daily-economic-events": {
            "task": "tasks.trading_generate_economic_events",
            "schedule": crontab(hour="12", minute="30"),
        },
        "trading-daily-credit-risk": {
            "task": "tasks.trading_generate_risk_management",
            "schedule": crontab(minute="*/15"),
        },

        # trading-daily-psychology removed — the cron worker
        # (scripts/fire_scheduled.py) handles trading_psychology
        # via the per-org schedule in orgs.settings->'schedule'.
        "trading-daily-strategies": {
            "task": "tasks.trading_generate_investment_strategies",
            "schedule": crontab(hour="6", minute="10"),
        },
        "trading-daily-styles": {
            "task": "tasks.trading_generate_trading_styles",
            "schedule": crontab(hour="6", minute="20"),
        },
        "trading-daily-class-room": {
            "task": "tasks.trading_generate_class_room",
            "schedule": crontab(hour="6", minute="30"),
        },
        "trading-daily-tools": {
            "task": "tasks.trading_generate_trading_tools",
            "schedule": crontab(hour="6", minute="40"),
        },

        # Refresh per-team form snapshots daily at 01:00 UTC
        # (feeds dixon_coles_v2's recent-form signal)
        "refresh-form-snapshots": {
            "task": "tasks.refresh_form_snapshots",
            "schedule": crontab(hour="1", minute="0"),
        },
        # Refresh per-pair H2H snapshots 15 min later (01:15 UTC)
        "refresh-h2h-snapshots": {
            "task": "tasks.refresh_h2h_snapshots",
            "schedule": crontab(hour="1", minute="15"),
        },
        # Run dixon_coles_v2 predictions for upcoming fixtures at 02:00 UTC
        # (after form + H2H snapshots have refreshed)
        "run-v2-predictions": {
            "task": "tasks.run_v2_predictions",
            "schedule": crontab(hour="2", minute="0"),
        },
        # Refresh per-team form snapshots daily at 01:00 UTC
        # (feeds dixon_coles_v2's recent-form signal)
        "refresh-form-snapshots": {
            "task": "tasks.refresh_form_snapshots",
            "schedule": crontab(hour="1", minute="0"),
        },
        # Recompute external source reliability daily at 03:00 UTC
        # (10 min after score_predictions has had a chance to run in the day)
        "score-external-reliability": {
            "task": "tasks.score_external_reliability",
            "schedule": crontab(hour="3", minute="0"),
        },
        # S.7.6c — permanently delete trash older than 2 days.
        # Runs at 04:00 UTC, after the nightly scoring jobs and before
        # the trading daily runs at 06:00.
        "purge-trash": {
            "task": "tasks.purge_trash",
            "schedule": crontab(hour="4", minute="0"),
        },
        # S.8.96 — trader process draft. Every 6 hours, at the top
        # of the hour. Drafts any section that has no user entry
        # and no recent draft. The trader corrects; the register
        # reads user entries first, drafts as fallback.
        "trader-process-draft": {
            "task": "tasks.trader_process_draft",
            "schedule": crontab(minute="0", hour="*/6"),
        },
    }
