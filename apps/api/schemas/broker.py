"""Pydantic schemas for the broker API."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class BrokerConnectRequest(BaseModel):
    broker: str = Field(..., description="'alpaca' | 'ibkr' | 'tradier'")
    label: str = Field("", max_length=64)
    api_key: str = Field(..., min_length=8)
    api_secret: str = Field(..., min_length=8)
    paper: bool = True


class BrokerCredentialResponse(BaseModel):
    id: str
    broker: str
    label: str
    paper: bool
    monitor_enabled: bool
    key_preview: str
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class BrokerStatusResponse(BaseModel):
    connected: bool
    broker: str | None = None
    label: str | None = None
    paper: bool | None = None
    monitor_enabled: bool | None = None
    key_preview: str | None = None
    last_sync_at: datetime | None = None
    last_sync_error: str | None = None


class BrokerAccountResponse(BaseModel):
    equity: float
    buying_power: float
    cash: float
    portfolio_value: float
    currency: str
    status: str
    pattern_day_trader: bool


class BrokerPositionResponse(BaseModel):
    symbol: str
    qty: float
    avg_entry_price: float
    market_value: float
    unrealized_pl: float
    unrealized_plpc: float


class BrokerOrderResponse(BaseModel):
    id: str
    symbol: str
    side: str
    qty: str
    filled_qty: str
    type: str
    status: str
    submitted_at: str | None = None
    filled_avg_price: float | None = None


class BrokerFillResponse(BaseModel):
    id: str
    symbol: str
    side: str
    qty: float
    price: float
    transaction_time: str | None = None


class BrokerSettingsRequest(BaseModel):
    monitor_enabled: bool

class BrokerOrderRequest(BaseModel):
    symbol: str = Field(..., min_length=1, max_length=32)
    side: str = Field(..., description="'buy' or 'sell'")
    qty: float = Field(..., gt=0)
    type: str = Field("market", description="'market' or 'limit'")
    limit_price: float | None = None
    time_in_force: str = "day"


class BrokerOrderResponse(BaseModel):
    id: str
    symbol: str
    side: str
    qty: str
    filled_qty: str
    type: str
    status: str
    submitted_at: str | None = None
    filled_avg_price: float | None = None

