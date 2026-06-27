from __future__ import annotations

from abc import ABC, abstractmethod

from app.core.models import Candle, MarketSymbol, Quote


class ProviderError(RuntimeError):
    pass


class ExchangeProvider(ABC):
    exchange_id: str
    exchange_name: str
    tradingview_exchange: str

    @abstractmethod
    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        raise NotImplementedError

    @abstractmethod
    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        raise NotImplementedError

    async def close(self) -> None:
        return None
