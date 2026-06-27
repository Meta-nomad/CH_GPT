from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import ccxt.async_support as ccxt

from app.core.models import Candle, MarketSymbol, Quote
from app.providers.base import ExchangeProvider, ProviderError


class CcxtExchangeProvider(ExchangeProvider):
    def __init__(self, exchange_id: str, tradingview_exchange: str, exchange_name: str | None = None) -> None:
        if not hasattr(ccxt, exchange_id):
            raise ProviderError(f"ccxt does not support exchange: {exchange_id}")
        self.exchange_id = exchange_id
        self.exchange_name = exchange_name or exchange_id.title()
        self.tradingview_exchange = tradingview_exchange
        exchange_class = getattr(ccxt, exchange_id)
        self.client = exchange_class({"enableRateLimit": True})
        self._markets: dict[str, Any] | None = None

    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        markets = await self._load_markets()
        found: list[MarketSymbol] = []
        quote_values = {quote.value for quote in quotes}

        for symbol, market in markets.items():
            if not market.get("active", True):
                continue
            if market.get("spot") is False:
                continue
            if str(market.get("base", "")).upper() != base:
                continue
            quote = str(market.get("quote", "")).upper()
            if quote not in quote_values:
                continue
            found.append(
                MarketSymbol(
                    exchange_id=self.exchange_id,
                    exchange_name=self.exchange_name,
                    base=base,
                    quote=Quote(quote),
                    market_symbol=symbol,
                    tradingview_exchange=self.tradingview_exchange,
                )
            )

        return found

    async def has_futures_market(self, base: str) -> bool | None:
        if self.exchange_id != "mexc":
            return None

        try:
            markets = await self._load_markets()
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        for market in markets.values():
            if str(market.get("base", "")).upper() != base:
                continue
            if str(market.get("quote", "")).upper() != "USDT":
                continue
            if market.get("swap") or market.get("future") or market.get("contract"):
                return True
        return False

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        try:
            rows = await self.client.fetch_ohlcv(market.market_symbol, timeframe="1h", limit=limit)
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        return [_row_to_candle(row) for row in rows]

    async def find_earliest_hourly_candle(self, market: MarketSymbol) -> Candle | None:
        since = int(datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        try:
            rows = await self.client.fetch_ohlcv(
                market.market_symbol,
                timeframe="1h",
                since=since,
                limit=1,
            )
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        if not rows:
            return None
        return _row_to_candle(rows[0])

    async def close(self) -> None:
        await self.client.close()

    async def _load_markets(self) -> dict[str, Any]:
        if self._markets is None:
            self._markets = await self.client.load_markets()
        return self._markets



def _row_to_candle(row: list[float]) -> Candle:
    timestamp_ms, open_, high, low, close, volume = row
    return Candle(
        timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
        open=float(open_),
        high=float(high),
        low=float(low),
        close=float(close),
        volume=float(volume or 0),
    )
