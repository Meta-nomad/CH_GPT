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

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        try:
            rows = await self.client.fetch_ohlcv(market.market_symbol, timeframe="1h", limit=limit)
        except Exception as exc:
            raise ProviderError(str(exc)) from exc

        candles: list[Candle] = []
        for timestamp_ms, open_, high, low, close, volume in rows:
            candles.append(
                Candle(
                    timestamp=datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc),
                    open=float(open_),
                    high=float(high),
                    low=float(low),
                    close=float(close),
                    volume=float(volume or 0),
                )
            )
        return candles

    async def close(self) -> None:
        await self.client.close()

    async def _load_markets(self) -> dict[str, Any]:
        if self._markets is None:
            self._markets = await self.client.load_markets()
        return self._markets
