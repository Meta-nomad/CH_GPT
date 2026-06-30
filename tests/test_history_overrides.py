from datetime import datetime, timezone

from app.core.history_overrides import get_tradingview_history_start
from app.core.models import MarketSymbol, Quote


def test_kaia_bitget_history_override() -> None:
    market = MarketSymbol("bitget", "Bitget", "KAIA", Quote.USDT, "KAIA/USDT", "BITGET")

    assert get_tradingview_history_start(market) == datetime(2025, 2, 1, tzinfo=timezone.utc)


def test_kaia_mexc_history_override() -> None:
    market = MarketSymbol("mexc", "MEXC", "KAIA", Quote.USDT, "KAIA/USDT", "MEXC")

    assert get_tradingview_history_start(market) == datetime(2025, 7, 1, tzinfo=timezone.utc)
