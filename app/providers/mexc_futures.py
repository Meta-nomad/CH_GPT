from __future__ import annotations

from typing import Any

import ccxt.async_support as ccxt

from app.providers.base import ProviderError


class MexcFuturesChecker:
    exchange_id = "mexc_futures"

    def __init__(self) -> None:
        self.client = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self._markets: dict[str, Any] | None = None

    async def has_futures_market(self, base: str) -> bool | None:
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

    async def close(self) -> None:
        await self.client.close()

    async def _load_markets(self) -> dict[str, Any]:
        if self._markets is None:
            self._markets = await self.client.load_markets()
        return self._markets
