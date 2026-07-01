from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum


class Quote(str, Enum):
    USD = "USD"
    USDT = "USDT"


@dataclass(frozen=True)
class Candle:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class MarketSymbol:
    exchange_id: str
    exchange_name: str
    base: str
    quote: Quote
    market_symbol: str
    tradingview_exchange: str

    @property
    def tradingview_symbol(self) -> str:
        cleaned = self.market_symbol.replace("/", "").replace(":", "")
        return f"{self.tradingview_exchange}:{cleaned}"


@dataclass(frozen=True)
class ChartMetrics:
    history_days: float
    first_candle_at: datetime | None
    last_candle_at: datetime | None
    expected_candles: int
    actual_candles: int
    gap_count: int
    flat_candle_ratio: float
    zero_volume_ratio: float
    spike_count: int
    average_volume: float

    @property
    def has_defects(self) -> bool:
        return (
            self.gap_count > 0
            or self.flat_candle_ratio > 0.001
            or self.zero_volume_ratio > 0.01
            or self.spike_count > 0
        )


@dataclass(frozen=True)
class ChartScore:
    symbol: MarketSymbol
    metrics: ChartMetrics
    score: float
    reasons: list[str] = field(default_factory=list)
    penalties: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AnalysisResult:
    query: str
    generated_at: datetime
    ranked: list[ChartScore]
    mexc_futures_available: bool | None = None

    @property
    def best(self) -> ChartScore | None:
        return self.ranked[0] if self.ranked else None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
