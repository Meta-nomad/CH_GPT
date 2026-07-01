from app.providers.mexc_futures import (
    _ccxt_market_matches,
    _extract_data_object,
    _extract_depth_object,
    _has_order_book,
    _is_exact_active_usdt_contract,
    _looks_disabled_or_hidden,
    _normalize_symbol,
)


def test_mexc_detail_requires_exact_active_usdt_contract() -> None:
    contract = {"symbol": "PUMP_USDT", "quoteCoin": "USDT", "settleCoin": "USDT", "state": 0}

    assert _is_exact_active_usdt_contract(contract, "PUMP_USDT") is True
    assert _is_exact_active_usdt_contract({**contract, "symbol": "PUMPFUN_USDT"}, "PUMP_USDT") is False
    assert _is_exact_active_usdt_contract({**contract, "state": 1}, "PUMP_USDT") is False


def test_mexc_depth_accepts_raw_or_wrapped_payload() -> None:
    raw = {"bids": [[1, 10]], "asks": [[2, 10]]}
    wrapped = {"success": True, "data": raw}

    assert _extract_depth_object(raw) == raw
    assert _extract_depth_object(wrapped) == raw
    assert _has_order_book(raw) is True


def test_mexc_data_object_rejects_failed_response() -> None:
    assert _extract_data_object({"success": False, "data": {"symbol": "BTC_USDT"}}) is None


def test_mexc_hidden_flags_are_rejected() -> None:
    assert _looks_disabled_or_hidden({"visible": False}) is True
    assert _looks_disabled_or_hidden({"hidden": True}) is True
    assert _looks_disabled_or_hidden({"visible": True, "hidden": False}) is False


def test_ccxt_swap_market_requires_exact_symbol_and_contract() -> None:
    market = {
        "base": "PUMP",
        "quote": "USDT",
        "settle": "USDT",
        "active": True,
        "swap": True,
        "contract": True,
        "id": "PUMP_USDT",
        "symbol": "PUMP/USDT:USDT",
    }

    assert _ccxt_market_matches("PUMP_USDT", market, "PUMP_USDT", "PUMP") is True
    assert _ccxt_market_matches("PUMPFUN_USDT", {**market, "id": "PUMPFUN_USDT"}, "PUMP_USDT", "PUMP") is False
    assert _ccxt_market_matches("PUMP_USDT", {**market, "swap": False, "contract": False}, "PUMP_USDT", "PUMP") is False


def test_normalize_symbol_handles_ccxt_contract_formats() -> None:
    assert _normalize_symbol("PUMP/USDT:USDT") == "PUMP_USDT"
    assert _normalize_symbol("PUMP-USDT") == "PUMP_USDT"
    assert _normalize_symbol("PUMPUSDT") == "PUMP_USDT"
