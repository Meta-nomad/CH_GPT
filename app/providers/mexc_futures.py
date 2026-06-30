from __future__ import annotations

import asyncio
import json
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MEXC_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
MEXC_CONTRACT_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_CONTRACT_DEPTH_URL = "https://contract.mexc.com/api/v1/contract/depth"
REQUEST_TIMEOUT_SECONDS = 8
ACTIVE_STATES = {0, "0"}
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
                asyncio.to_thread(_has_tradeable_contract_sync, symbol),
                timeout=REQUEST_TIMEOUT_SECONDS * 3,
            )
        return self._cache[symbol]

    async def close(self) -> None:
        return None


def _has_tradeable_contract_sync(symbol: str) -> bool:
    detail_payload = _request_json(f"{MEXC_CONTRACT_DETAIL_URL}?{urlencode({'symbol': symbol})}")
    contract = _extract_data_object(detail_payload)
    if contract is None or not _is_exact_active_usdt_contract(contract, symbol):
        return False

    ticker_payload = _request_json(f"{MEXC_CONTRACT_TICKER_URL}?{urlencode({'symbol': symbol})}")
    ticker = _extract_data_object(ticker_payload)
    if ticker is None or not _is_exact_symbol(ticker, symbol) or not _has_real_price(ticker):
        return False

    depth_payload = _request_json(f"{MEXC_CONTRACT_DEPTH_URL}/{quote(symbol)}")
    depth = _extract_depth_object(depth_payload)
    return depth is not None and _has_order_book(depth)


def _request_json(url: str) -> dict[str, Any]:
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


def _extract_data_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _response_success(payload):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return None


def _extract_depth_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _response_success(payload):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if "bids" in payload or "asks" in payload:
        return payload
    return None


def _response_success(payload: dict[str, Any]) -> bool:
    success = payload.get("success")
    if success is False:
        return False
    code = payload.get("code")
    return code in (None, 0, 200, "0", "200")


def _is_exact_active_usdt_contract(contract: dict[str, Any], expected_symbol: str) -> bool:
    if not _is_exact_symbol(contract, expected_symbol):
        return False
    quote_coin = str(contract.get("quoteCoin") or contract.get("quote") or "").upper()
    settle_coin = str(contract.get("settleCoin") or contract.get("settle") or "").upper()
    if quote_coin not in {"", "USDT"}:
        return False
    if settle_coin not in {"", "USDT"}:
        return False
    if contract.get("enable") is False or contract.get("enabled") is False or contract.get("active") is False:
        return False
    state = contract.get("state")
    if state in ACTIVE_STATES or state is None:
        return True
    if isinstance(state, str) and state.strip().lower() in INACTIVE_STATE_TEXT:
        return False
    return False


def _is_exact_symbol(data: dict[str, Any], expected_symbol: str) -> bool:
    return str(data.get("symbol") or "").upper() == expected_symbol


def _has_real_price(ticker: dict[str, Any]) -> bool:
    for key in ("lastPrice", "last", "fairPrice", "indexPrice", "bid1", "ask1"):
        if _positive_number(ticker.get(key)):
            return True
    return False


def _has_order_book(depth: dict[str, Any]) -> bool:
    bids = depth.get("bids") or depth.get("bid") or []
    asks = depth.get("asks") or depth.get("ask") or []
    return _has_book_side(bids) and _has_book_side(asks)


def _has_book_side(side: Any) -> bool:
    if not isinstance(side, list) or not side:
        return False
    first = side[0]
    if isinstance(first, list) and first:
        return _positive_number(first[0])
    if isinstance(first, dict):
        return _positive_number(first.get("price") or first.get("p"))
    return False


def _positive_number(value: Any) -> bool:
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _normalize_base(value: Any) -> str:
    return "".join(ch for ch in str(value).upper() if ch.isalnum())
