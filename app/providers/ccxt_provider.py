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

    async def find_earliest_history_candle(self, market: MarketSymbol) -> Candle | None:
        candidates: list[Candle] = []
        metadata_candle = await self._market_listing_candle(market)
        if metadata_candle is not None:
            candidates.append(metadata_candle)

        since = int(datetime(2010, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        supported = set((self.client.timeframes or {}).keys())
        preferred_timeframes = ["1M", "1w", "1d"]

        for timeframe in preferred_timeframes:
            if supported and timeframe not in supported:
                continue
            candle = await self._fetch_oldest_history_candle(market, timeframe, since)
            if candle is not None:
                candidates.append(candle)

        if not candidates:
            return None
        return min(candidates, key=lambda candle: candle.timestamp)

    async def _fetch_oldest_history_candle(
        self,
        market: MarketSymbol,
        timeframe: str,
        since: int,
    ) -> Candle | None:
        rows: list[list[float]] = []
        for request_since in (since, None):
            try:
                batch = await self.client.fetch_ohlcv(
                    market.market_symbol,
                    timeframe=timeframe,
                    since=request_since,
                    limit=1000,
                )
            except Exception:
                continue
            rows.extend(batch or [])

        if not rows:
            return None
        return min((_row_to_candle(row) for row in rows), key=lambda candle: candle.timestamp)

    async def _market_listing_candle(self, market: MarketSymbol) -> Candle | None:
        markets = await self._load_markets()
        market_data = markets.get(market.market_symbol, {})
        timestamp = _extract_listing_timestamp(market_data)
        if timestamp is None:
            return None
        return Candle(
            timestamp=datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc),
            open=0,
            high=0,
            low=0,
            close=0,
            volume=0,
        )

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


def _extract_listing_timestamp(market_data: dict[str, Any]) -> int | None:
    keys = (
        "created",
        "createdAt",
        "listedAt",
        "listingTime",
        "launchTime",
        "onlineTime",
        "onlineAt",
        "openTime",
    )
    values: list[Any] = []
    for key in keys:
        values.append(market_data.get(key))
    info = market_data.get("info") or {}
    if isinstance(info, dict):
        for key in keys:
            values.append(info.get(key))

    timestamps = [_normalize_timestamp(value) for value in values]
    timestamps = [value for value in timestamps if value is not None]
    return min(timestamps) if timestamps else None


def _normalize_timestamp(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if numeric <= 0:
        return None
    if numeric < 10_000_000_000:
        numeric *= 1000
    return int(numeric)
