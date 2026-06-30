from datetime import datetime, timezone

from app.providers.tradingview import _decode_messages, _parse_series, _resolve_symbol_payloads


def test_decode_tradingview_framed_messages() -> None:
    payload = '{"m":"series_completed","p":["cs_test","s1"]}'
    raw = f"~m~{len(payload)}~m~{payload}"

    assert _decode_messages(raw) == [{"m": "series_completed", "p": ["cs_test", "s1"]}]


def test_parse_series_to_candles() -> None:
    candles = _parse_series(
        {
            "t": [1583020800],
            "o": [1],
            "h": [2],
            "l": [0.5],
            "c": [1.5],
            "v": [10],
        }
    )

    assert candles[0].timestamp == datetime(2020, 3, 1, tzinfo=timezone.utc)
    assert candles[0].open == 1
    assert candles[0].high == 2
    assert candles[0].low == 0.5
    assert candles[0].close == 1.5
    assert candles[0].volume == 10



def test_resolve_symbol_payloads_include_equals_json_first() -> None:
    payloads = _resolve_symbol_payloads("BINANCE:BTCUSDT")

    assert payloads[0][0] == "equals_json_session"
    assert payloads[0][1].startswith("={")
    assert payloads[-1] == ("plain_symbol", "BINANCE:BTCUSDT")
