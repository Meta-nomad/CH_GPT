from app.providers.mexc_futures import (
    _extract_data_object,
    _extract_depth_object,
    _has_order_book,
    _is_exact_active_usdt_contract,
)


def test_mexc_detail_requires_exact_active_usdt_contract() -> None:
    contract = {"symbol": "MORPHO_USDT", "quoteCoin": "USDT", "settleCoin": "USDT", "state": 0}

    assert _is_exact_active_usdt_contract(contract, "MORPHO_USDT") is True
    assert _is_exact_active_usdt_contract({**contract, "symbol": "MORPH_USDT"}, "MORPHO_USDT") is False
    assert _is_exact_active_usdt_contract({**contract, "state": 1}, "MORPHO_USDT") is False


def test_mexc_depth_accepts_raw_or_wrapped_payload() -> None:
    raw = {"bids": [[1, 10]], "asks": [[2, 10]]}
    wrapped = {"success": True, "data": raw}

    assert _extract_depth_object(raw) == raw
    assert _extract_depth_object(wrapped) == raw
    assert _has_order_book(raw) is True


def test_mexc_data_object_rejects_failed_response() -> None:
    assert _extract_data_object({"success": False, "data": {"symbol": "BTC_USDT"}}) is None
