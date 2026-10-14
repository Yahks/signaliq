"""FastAPI application entrypoint."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse

from apps.api.middleware.jurisdiction import JurisdictionMiddleware
from apps.api.middleware.rate_limit import RateLimitMiddleware
from apps.api.middleware.tenant import TenantContextMiddleware
from apps.api.routers import auth as auth_router
from apps.api.routers import blocks as blocks_router
from apps.api.routers import health as health_router
from apps.api.routers import briefs as briefs_router
from apps.api.routers import predictions as predictions_router
from apps.api.routers import market_conditions as market_conditions_router
from apps.api.routers import clusters as clusters_router
from apps.api.routers import dashboard as dashboard_router
from apps.api.routers import enrichments as enrichments_router
from apps.api.routers import ingestion as ingestion_router
from apps.api.routers import metrics as metrics_router
from apps.api.routers import news_chains as news_chains_router
from apps.api.routers import notifications as notifications_router
from apps.api.routers import onboarding as onboarding_router
from apps.api.routers import trading as trading_router
from apps.api.routers import trash as trash_router
from apps.api.routers import settings as settings_router
from apps.api.routers import studio as studio_router
from apps.api.routers import broker as broker_router
from apps.api.routers import trader_process as trader_process_router
from apps.api.routers import credentials as credentials_router
from apps.api.routers import source_reliability as source_reliability_router
from apps.api.routers import pain_points as pain_points_router
from apps.api.routers import records as records_router
from apps.api.routers import search as search_router
from apps.api.routers import sources as sources_router
from packages.core.config import get_settings
from packages.core.error import SignalIQError
from packages.core.logging import configure_logging, get_logger
from packages.db.session import dispose_engine

configure_logging()
log = get_logger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("signaliq.startup", env=settings.env)

    # Run migrations on startup. Idempotent — no-op if the schema is
    # already at head. Set RUN_MIGRATIONS_ON_STARTUP=false to skip
    # (useful for read replicas and tests).
    import os
    if os.environ.get("RUN_MIGRATIONS_ON_STARTUP", "true").lower() == "true":
        try:
            from alembic.config import Config as _AlembicConfig
            from alembic import command as _alembic_command
            from pathlib import Path as _Path
            _root = _Path(__file__).resolve().parents[2]
            _cfg = _AlembicConfig(str(_root / "alembic.ini"))
            _cfg.set_main_option("script_location", str(_root / "alembic"))
            _alembic_command.upgrade(_cfg, "head")
            log.info("signaliq.migrated", revision="head")
        except Exception as exc:
            log.error("signaliq.migrate_failed", error=str(exc)[:300])

    yield
    await dispose_engine()
    log.info("signaliq.shutdown")


app = FastAPI(
    title="SignalIQ API",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if not settings.is_production else None,
    redoc_url="/redoc" if not settings.is_production else None,
)

app.add_middleware(TenantContextMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(JurisdictionMiddleware)


@app.exception_handler(SignalIQError)
async def domain_error_handler(_, exc: SignalIQError) -> JSONResponse:
    log.warning("domain_error", code=exc.code, message=exc.message, **exc.context)
    return JSONResponse(status_code=exc.http_status, content=exc.to_dict())


app.include_router(auth_router.router)
app.include_router(sources_router.router)
app.include_router(ingestion_router.router)
app.include_router(enrichments_router.router)
app.include_router(clusters_router.router)
app.include_router(blocks_router.router)
app.include_router(health_router.router)
app.include_router(briefs_router.router)
app.include_router(predictions_router.router)
app.include_router(market_conditions_router.router)
app.include_router(dashboard_router.router)
app.include_router(records_router.router)
app.include_router(pain_points_router.router)
app.include_router(news_chains_router.router)
app.include_router(metrics_router.router)
app.include_router(notifications_router.router)
app.include_router(onboarding_router.router)
app.include_router(trading_router.router)
app.include_router(trash_router.router)
app.include_router(settings_router.router)
app.include_router(studio_router.router)
app.include_router(credentials_router.router)
app.include_router(broker_router.router)
app.include_router(trader_process_router.router)
app.include_router(source_reliability_router.router)
app.include_router(search_router.router)

# S.8.10 — serve chart images written by packages/trading/chart.py.
# Charts live under apps/api/static/charts/ and are exposed as
# /static/charts/<file>.png.
_static_root = Path("apps/api/static")
_static_root.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=str(_static_root)), name="static")



@app.get("/health", tags=["system"])
async def health() -> dict:
    return {"status": "ok", "env": settings.env, "version": app.version}


@app.get("/", tags=["system"])
async def root() -> dict:
    return {"name": "SignalIQ", "docs": "/docs", "health": "/health"}

from pathlib import Path