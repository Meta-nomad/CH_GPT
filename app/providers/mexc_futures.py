from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.request import Request, urlopen

MEXC_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
REQUEST_TIMEOUT_SECONDS = 8


class MexcFuturesChecker:
    exchange_id = "mexc_futures"

    def __init__(self) -> None:
        self._symbols: set[str] | None = None

    async def has_futures_market(self, base: str) -> bool | None:
        normalized = _normalize_base(base)
        if not normalized:
            return False
        symbols = await self._load_symbols()
        return f"{normalized}_USDT" in symbols

    async def close(self) -> None:
        return None

    async def _load_symbols(self) -> set[str]:
        if self._symbols is None:
            self._symbols = await asyncio.wait_for(
                asyncio.to_thread(_fetch_contract_symbols_sync),
                timeout=REQUEST_TIMEOUT_SECONDS + 2,
            )
        return self._symbols


def _fetch_contract_symbols_sync() -> set[str]:
    request = Request(
        MEXC_CONTRACT_DETAIL_URL,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        payload = json.loads(response.read().decode("utf-8"))

    data = payload.get("data") if isinstance(payload, dict) else payload
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return set()

    symbols: set[str] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        symbol = str(item.get("symbol") or "").upper()
        base = _normalize_base(item.get("baseCoin") or item.get("base") or "")
        quote = str(item.get("quoteCoin") or item.get("quote") or item.get("settleCoin") or "").upper()
        if not symbol and base and quote:
            symbol = f"{base}_{quote}"
        if not symbol.endswith("_USDT"):
            continue
        if _looks_disabled(item):
            continue
        symbols.add(symbol)
    return symbols


def _looks_disabled(item: dict[str, Any]) -> bool:
    state = item.get("state")
    if isinstance(state, str) and state.lower() in {"offline", "disabled", "delisted", "closed"}:
        return True
    if item.get("enable") is False or item.get("enabled") is False or item.get("active") is False:
        return True
    return False


def _normalize_base(value: Any) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())
