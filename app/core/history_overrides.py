from __future__ import annotations

from datetime import datetime, timezone

from app.core.models import MarketSymbol


# TradingView can start a symbol later than the exchange API history, especially
# after token renames or migrations. These overrides are authoritative starts for
# the visible TradingView chart, not for the underlying coin or exchange market.
TRADINGVIEW_HISTORY_START_OVERRIDES: dict[str, str] = {
    "BITGET:KAIAUSDT": "2025-02-01",
    "MEXC:KAIAUSDT": "2025-07-01",
}


def get_tradingview_history_start(market: MarketSymbol) -> datetime | None:
    value = TRADINGVIEW_HISTORY_START_OVERRIDES.get(market.tradingview_symbol)
    if value is None:
        return None
    return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
