"""Celery application.

Broker + result backend: Redis. Two queues:
    - `default`     : light tasks
    - `ingestion`   : file parsing and normalization
"""
from __future__ import annotations

from celery import Celery

from apps.worker.beat_schedule import build_schedule
from packages.core.config import get_settings

settings = get_settings()

celery_app = Celery(
    "signaliq",
    broker=settings.redis_url.replace("/0", "/1"),
    backend=settings.redis_url.replace("/0", "/2"),
    task_default_queue="default",
    include=[
        "apps.worker.tasks.ingestion",
        "apps.worker.tasks.normalize",
        "apps.worker.tasks.enrich",
        "apps.worker.tasks.embed",
        "apps.worker.tasks.cluster",
        "apps.worker.tasks.pain_points",
        "apps.worker.tasks.news_chains",
        "apps.worker.tasks.fetch_stock_news",
        "apps.worker.tasks.scheduled",
        "apps.worker.tasks.budget",
        "apps.worker.tasks.market_warm",
        "apps.worker.tasks.briefs",
        "apps.worker.tasks.score_predictions",
        "apps.worker.tasks.trading",
        "apps.worker.tasks.trader_process",
        "apps.worker.tasks.prediction_snapshots",
        "apps.worker.tasks.prediction_v2",
        "apps.worker.tasks.external_reliability",
        "apps.worker.tasks.maintenance",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=3600,        # hard kill after 1h
    task_soft_time_limit=3300,   # soft kill after 55m
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    beat_schedule=build_schedule(),
    task_routes={
        "tasks.ingest_file": {"queue": "ingestion"},
        "tasks.normalize_job": {"queue": "ingestion"},
        "tasks.enrich_org": {"queue": "ingestion"},
        "tasks.embed_org": {"queue": "ingestion"},
        "tasks.recluster_org": {"queue": "ingestion"},
        "tasks.aggregate_pain_points_org": {"queue": "ingestion"},
        "tasks.build_chain": {"queue": "ingestion"},
        "tasks.fetch_stock_news": {"queue": "ingestion"},
        "tasks.scheduled_stock_fetch": {"queue": "ingestion"},
        "tasks.recluster_all_orgs": {"queue": "ingestion"},
        "tasks.flush_ai_budget": {"queue": "ingestion"},
        "tasks.warm_market_cache": {"queue": "ingestion"},
        "tasks.generate_daily_brief": {"queue": "ingestion"},
        "tasks.generate_daily_briefs_all": {"queue": "ingestion"},
        "tasks.score_predictions": {"queue": "ingestion"},
        "tasks.trading_generate_stock_lists": {"queue": "ingestion"},
        "tasks.trading_generate_market_signals": {"queue": "ingestion"},
        "tasks.trading_generate_screener_picks": {"queue": "ingestion"},
        "tasks.trading_generate_market_analysis": {"queue": "ingestion"},
        "tasks.trading_generate_market_condition": {"queue": "ingestion"},
        "tasks.trading_generate_movers": {"queue": "ingestion"},
        "tasks.trading_generate_market_cap": {"queue": "ingestion"},
        "tasks.trading_generate_asset_classes": {"queue": "ingestion"},
        "tasks.trading_generate_technical_indicators": {"queue": "ingestion"},
        "tasks.trading_generate_trading_psychology": {"queue": "ingestion"},
        "tasks.trading_generate_investment_strategies": {"queue": "ingestion"},
        "tasks.trading_generate_trading_styles": {"queue": "ingestion"},
        "tasks.trading_generate_class_room": {"queue": "ingestion"},
        "tasks.trading_generate_trading_tools": {"queue": "ingestion"},
        "tasks.trading_generate_economic_events": {"queue": "ingestion"},
        "tasks.trading_generate_risk_management": {"queue": "ingestion"},
        "tasks.trading_generate_fundamental_metrics": {"queue": "ingestion"},
        "tasks.refresh_form_snapshots": {"queue": "ingestion"},
        "tasks.refresh_h2h_snapshots": {"queue": "ingestion"},
        "tasks.run_v2_predictions": {"queue": "ingestion"},
        "tasks.score_external_reliability": {"queue": "ingestion"},
    },
)
