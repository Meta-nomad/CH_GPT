from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import json
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

import ccxt.async_support as ccxt

MEXC_CONTRACT_DETAIL_URL = "https://contract.mexc.com/api/v1/contract/detail"
MEXC_CONTRACT_TICKER_URL = "https://contract.mexc.com/api/v1/contract/ticker"
MEXC_CONTRACT_DEPTH_URL = "https://contract.mexc.com/api/v1/contract/depth"
REQUEST_TIMEOUT_SECONDS = 3
MEXC_FUTURES_CACHE_VERSION = "mexc-live-contract-v4"
MEXC_FUTURES_CACHE_TTL_SECONDS = 300
ACTIVE_STATES = {0, "0"}
INACTIVE_STATE_TEXT = {"offline", "disabled", "delisted", "closed", "suspended"}


class MexcFuturesChecker:
    exchange_id = "mexc_futures"

    def __init__(self) -> None:
        self.client = ccxt.mexc({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        self._cache: dict[str, dict[str, Any]] = {}
        self._markets: dict[str, Any] | None = None

    async def has_futures_market(self, base: str) -> bool | None:
        diagnostic = await self.diagnostic(base)
        if not diagnostic.get("checked", True):
            return None
        return bool(diagnostic.get("available"))

    async def diagnostic(self, base: str, *, force_refresh: bool = False) -> dict[str, Any]:
        normalized = _normalize_base(base)
        symbol = f"{normalized}_USDT"
        cached = self._cache.get(symbol)
        if not force_refresh and _cache_entry_valid(cached):
            return {**cached["data"], "reason": "cache", "cache_version": MEXC_FUTURES_CACHE_VERSION}

        ccxt_result, api_result = await asyncio.gather(
            self._check_ccxt_swap(symbol, normalized),
            asyncio.to_thread(_check_contract_api_sync, symbol),
            return_exceptions=True,
        )
        ccxt_info = _source_result("ccxt", ccxt_result)
        api_info = _source_result("contract_api", api_result)
        api_checked = bool(api_info["checked"])
        available = bool(api_info["ok"])
        checked = api_checked
        decision_source = "contract_api_live" if api_checked else "unavailable"
        data = {
            "symbol": symbol,
            "available": available,
            "checked": checked,
            "decision_source": decision_source,
            **{key: value for key, value in ccxt_info.items() if key not in {"checked", "ok"}},
            **{key: value for key, value in api_info.items() if key not in {"checked", "ok"}},
        }
        if checked:
            self._cache[symbol] = {
                "version": MEXC_FUTURES_CACHE_VERSION,
                "expires_at": datetime.now(timezone.utc) + timedelta(seconds=MEXC_FUTURES_CACHE_TTL_SECONDS),
                "data": data,
            }
        return data

    async def close(self) -> None:
        await self.client.close()

    async def _check_ccxt_swap(self, symbol: str, base: str) -> dict[str, Any]:
        markets = await self._load_markets()
        matches = []
        for market_id, market in markets.items():
            if not _ccxt_market_matches(market_id, market, symbol, base):
                continue
            matches.append(_ccxt_market_label(market_id, market))
        return {"checked": True, "ok": bool(matches), "matches": matches[:5]}

    async def _load_markets(self) -> dict[str, Any]:
        if self._markets is None:
            self._markets = await self.client.load_markets()
        return self._markets


def _cache_entry_valid(entry: dict[str, Any] | None) -> bool:
    if not entry:
        return False
    if entry.get("version") != MEXC_FUTURES_CACHE_VERSION:
        return False
    expires_at = entry.get("expires_at")
    return isinstance(expires_at, datetime) and expires_at > datetime.now(timezone.utc)


def _check_contract_api_sync(symbol: str) -> dict[str, Any]:
    detail_payload = _safe_request_json(f"{MEXC_CONTRACT_DETAIL_URL}?{urlencode({'symbol': symbol})}")
    contract = _extract_data_object(detail_payload)
    matched_symbol = symbol if contract is not None and _is_exact_active_usdt_contract(contract, symbol) else None
    if matched_symbol is None:
        detail_list_payload = _safe_request_json(MEXC_CONTRACT_DETAIL_URL)
        contract = _find_matching_contract(_extract_data_list(detail_list_payload), symbol)
        matched_symbol = None if contract is None else _normalize_symbol(contract.get("symbol") or "")

    if matched_symbol:
        ticker_payload = _safe_request_json(f"{MEXC_CONTRACT_TICKER_URL}?{urlencode({'symbol': matched_symbol})}")
        depth_payload = _safe_request_json(f"{MEXC_CONTRACT_DEPTH_URL}/{quote(matched_symbol)}")
        ticker = _extract_data_object(ticker_payload)
        depth = _extract_depth_object(depth_payload)
    else:
        ticker = None
        depth = None

    detail_ok = contract is not None and matched_symbol is not None
    ticker_ok = ticker is not None and _is_exact_symbol(ticker, matched_symbol or symbol) and _has_real_price(ticker)
    depth_ok = depth is not None and _has_order_book(depth)

    # A single endpoint can be stale, so contract API needs two positive signals.
    return {
        "checked": True,
        "ok": bool((detail_ok and ticker_ok) or (ticker_ok and depth_ok) or (detail_ok and depth_ok)),
        "detail_ok": detail_ok,
        "ticker_ok": ticker_ok,
        "depth_ok": depth_ok,
        "detail_state": None if contract is None else contract.get("state"),
        "detail_symbol": None if contract is None else contract.get("symbol"),
        "ticker_symbol": None if ticker is None else ticker.get("symbol"),
    }


def _safe_request_json(url: str) -> dict[str, Any]:
    try:
        return _request_json(url)
    except Exception as exc:
        return {"success": False, "error": str(exc)}


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


def _source_result(prefix: str, value: Any) -> dict[str, Any]:
    if isinstance(value, Exception):
        return {
            f"{prefix}_checked": False,
            f"{prefix}_ok": False,
            f"{prefix}_error": str(value),
            "checked": False,
            "ok": False,
        }
    if not isinstance(value, dict):
        return {f"{prefix}_checked": False, f"{prefix}_ok": False, "checked": False, "ok": False}
    result = {
        f"{prefix}_{key}": item
        for key, item in value.items()
        if key not in {"checked", "ok"}
    }
    result[f"{prefix}_checked"] = bool(value.get("checked"))
    result[f"{prefix}_ok"] = bool(value.get("ok"))
    result["checked"] = bool(value.get("checked"))
    result["ok"] = bool(value.get("ok"))
    return result


def _ccxt_market_matches(market_id: str, market: dict[str, Any], symbol: str, base: str) -> bool:
    market_base = _normalize_base(market.get("base") or "")
    quote = str(market.get("quote") or market.get("settle") or "").upper()
    settle = str(market.get("settle") or "").upper()
    if "USDT" not in {quote, settle}:
        return False
    if market.get("active") is False:
        return False
    if not (market.get("swap") or market.get("future") or market.get("contract")):
        return False
    candidates = {
        _normalize_symbol(market_id),
        _normalize_symbol(market.get("id") or ""),
        _normalize_symbol(market.get("symbol") or ""),
    }
    if market_base == base and (symbol in candidates or f"{base}_USDT" in candidates):
        return True
    return False


def _ccxt_market_label(market_id: str, market: dict[str, Any]) -> str:
    return str(market.get("symbol") or market.get("id") or market_id)


def _extract_data_object(payload: dict[str, Any]) -> dict[str, Any] | None:
    if not _response_success(payload):
        return None
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    if isinstance(data, list) and len(data) == 1 and isinstance(data[0], dict):
        return data[0]
    return None


def _extract_data_list(payload: dict[str, Any]) -> list[dict[str, Any]]:
    if not _response_success(payload):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    return [item for item in data if isinstance(item, dict)]


def _find_matching_contract(contracts: list[dict[str, Any]], expected_symbol: str) -> dict[str, Any] | None:
    for contract in contracts:
        contract_symbol = _normalize_symbol(contract.get("symbol") or "")
        if not _is_active_usdt_contract(contract, contract_symbol):
            continue
        if contract_symbol == expected_symbol:
            return contract
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
    return _is_active_usdt_contract(contract, expected_symbol)


def _is_active_usdt_contract(contract: dict[str, Any], expected_symbol: str) -> bool:
    if not expected_symbol.endswith("_USDT"):
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


def _normalize_symbol(value: Any) -> str:
    cleaned = str(value).upper().strip().replace("-", "_").replace("/", "_").replace(":", "_")
    cleaned = "".join(ch for ch in cleaned if ch.isalnum() or ch == "_")
    if cleaned.endswith("_USDT_USDT"):
        cleaned = cleaned[: -10] + "_USDT"
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
