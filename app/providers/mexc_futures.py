from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

MEXC_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
REQUEST_TIMEOUT_SECONDS = 8
ACTIVE_STATES = {0, 1}
INACTIVE_STATE_TEXT = {"offline", "disabled", "delisted", "closed", "suspended"}


class MexcFuturesChecker:
    exchange_id = "mexc_futures"

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    async def has_futures_market(self, base: str) -> bool | None:
        normalized = _normalize_base(base)
        if not normalized:
            return False
        symbol = f"{normalized}_USDT"
        if symbol not in self._cache:
            self._cache[symbol] = await asyncio.wait_for(
                asyncio.to_thread(_has_active_contract_sync, symbol),
                timeout=REQUEST_TIMEOUT_SECONDS + 2,
            )
        return self._cache[symbol]

    async def close(self) -> None:
        return None


def _has_active_contract_sync(symbol: str) -> bool:
    payload = _request_contract_detail(symbol)
    if not _response_success(payload):
        return False
    contract = _extract_contract(payload)
    if contract is None:
        return False
    return _is_exact_active_usdt_contract(contract, symbol)


def _request_contract_detail(symbol: str) -> dict[str, Any]:
    url = f"{MEXC_CONTRACT_DETAIL_URL}?{urlencode({'symbol': symbol})}"
    request = Request(
        url,
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
    return payload if isinstance(payload, dict) else {}


def _response_success(payload: dict[str, Any]) -> bool:
    success = payload.get("success")
    if success is False:
        return False
    code = payload.get("code")
    if code not in (None, 0, 200, "0", "200"):
        return False
    return True


def _extract_contract(payload: dict[str, Any]) -> dict[str, Any] | None:
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return None


def _is_exact_active_usdt_contract(contract: dict[str, Any], expected_symbol: str) -> bool:
    symbol = str(contract.get("symbol") or "").upper()
    if symbol != expected_symbol:
        return False

    quote = str(contract.get("quoteCoin") or contract.get("quote") or "").upper()
    settle = str(contract.get("settleCoin") or contract.get("settle") or "").upper()
    if quote not in {"", "USDT"}:
        return False
    if settle not in {"", "USDT"}:
        return False

    if contract.get("enable") is False or contract.get("enabled") is False or contract.get("active") is False:
        return False

    state = contract.get("state")
    if isinstance(state, str):
        stripped = state.strip().lower()
        if stripped in INACTIVE_STATE_TEXT:
            return False
        if stripped.isdigit() and int(stripped) not in ACTIVE_STATES:
            return False
    elif isinstance(state, int) and state not in ACTIVE_STATES:
        return False

    return True


def _normalize_base(value: Any) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())
