from datetime import datetime, timezone

from app.providers.ccxt_provider import _row_to_candle


def test_row_to_candle_converts_timestamp_ms() -> None:
    candle = _row_to_candle([1583020800000, 1, 2, 0.5, 1.5, 10])

    assert candle.timestamp == datetime(2020, 3, 1, tzinfo=timezone.utc)
    assert candle.open == 1
    assert candle.high == 2
    assert candle.low == 0.5
    assert candle.close == 1.5
    assert candle.volume == 10
