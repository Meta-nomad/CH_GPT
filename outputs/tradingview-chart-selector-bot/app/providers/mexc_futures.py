from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

MEXC_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
MEXC_CONTRACT_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_CONTRACT_DEPTH_URL = "https://contract.mexc.com/api/v1/contract/depth"
REQUEST_TIMEOUT_SECONDS = 8
ACTIVE_STATES = {0, "0"}
INACTIVE_STATE_TEXT = {"offline", "disabled", "delisted", "closed", "suspended"}
DEFAULT_FALSE_POSITIVE_SYMBOLS = {"MAGMA_USDT", "MORPHO_USDT", "M_USDT"}


class MexcFuturesChecker:
    exchange_id = "mexc_futures"

    def __init__(self) -> None:
        self._cache: dict[str, bool] = {}

    async def has_futures_market(self, base: str) -> bool | None:
        normalized = _normalize_base(base)
        if not normalized:
            return False
        symbol = f"{normalized}_USDT"
        if symbol in _false_positive_symbols():
            return False
        if symbol not in self._cache:
            self._cache[symbol] = await asyncio.wait_for(
                asyncio.to_thread(_has_tradeable_contract_sync, symbol),
                timeout=REQUEST_TIMEOUT_SECONDS * 3,
            )
        return self._cache[symbol]

    async def diagnostic(self, base: str) -> dict[str, Any]:
        normalized = _normalize_base(base)
        symbol = f"{normalized}_USDT"
        if symbol in _false_positive_symbols():
            return {"symbol": symbol, "available": False, "reason": "manual_api_ui_mismatch"}
        return await asyncio.wait_for(
            asyncio.to_thread(_diagnose_contract_sync, symbol),
            timeout=REQUEST_TIMEOUT_SECONDS * 3,
        )

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


def _diagnose_contract_sync(symbol: str) -> dict[str, Any]:
    detail_payload = _request_json(f"{MEXC_CONTRACT_DETAIL_URL}?{urlencode({'symbol': symbol})}")
    contract = _extract_data_object(detail_payload)
    ticker_payload = _request_json(f"{MEXC_CONTRACT_TICKER_URL}?{urlencode({'symbol': symbol})}")
    ticker = _extract_data_object(ticker_payload)
    depth_payload = _request_json(f"{MEXC_CONTRACT_DEPTH_URL}/{quote(symbol)}")
    depth = _extract_depth_object(depth_payload)
    available = (
        contract is not None
        and _is_exact_active_usdt_contract(contract, symbol)
        and ticker is not None
        and _is_exact_symbol(ticker, symbol)
        and _has_real_price(ticker)
        and depth is not None
        and _has_order_book(depth)
    )
    return {
        "symbol": symbol,
        "available": available,
        "detail_success": _response_success(detail_payload),
        "detail_state": None if contract is None else contract.get("state"),
        "detail_hidden": None if contract is None else _looks_disabled_or_hidden(contract),
        "ticker_success": _response_success(ticker_payload),
        "ticker_symbol": None if ticker is None else ticker.get("symbol"),
        "has_price": False if ticker is None else _has_real_price(ticker),
        "depth_success": _response_success(depth_payload),
        "has_book": False if depth is None else _has_order_book(depth),
    }


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
    if _looks_disabled_or_hidden(contract):
        return False
    state = contract.get("state")
    if state in ACTIVE_STATES or state is None:
        return True
    if isinstance(state, str) and state.strip().lower() in INACTIVE_STATE_TEXT:
        return False
    return False


def _looks_disabled_or_hidden(data: dict[str, Any]) -> bool:
    false_flags = (
        "enable",
        "enabled",
        "active",
        "isOpen",
        "is_open",
        "open",
        "visible",
        "show",
        "display",
        "apiAllowed",
        "api_allowed",
    )
    true_flags = ("hidden", "isHidden", "is_hidden", "offline", "delisted")
    for key in false_flags:
        if data.get(key) is False:
            return True
    for key in true_flags:
        if data.get(key) is True:
            return True
    return False


def _false_positive_symbols() -> set[str]:
    raw = os.getenv("MEXC_FUTURES_FALSE_POSITIVES", "")
    configured = {_normalize_symbol(item) for item in raw.split(",") if item.strip()}
    return DEFAULT_FALSE_POSITIVE_SYMBOLS | configured


def _normalize_symbol(value: Any) -> str:
    cleaned = str(value).upper().strip().replace("-", "_").replace("/", "_")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch == "_")
    if "_" not in cleaned and cleaned.endswith("USDT"):
        cleaned = f"{cleaned[:-4]}_USDT"
    return cleaned


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
