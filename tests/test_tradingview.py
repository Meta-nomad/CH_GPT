from datetime import datetime, timezone

from app.core.models import Quote
from app.providers.tradingview import (
    _asset_variants,
    _decode_messages,
    _direct_crypto_markets,
    _parse_series,
    _quote_aliases,
    _resolve_symbol_payloads,
    _search_terms,
    _symbols_to_markets,
)


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


def test_asset_variants_include_known_renames() -> None:
    assert _asset_variants("RENDER") == ["RENDER", "RNDR"]
    assert _asset_variants("RNDR") == ["RNDR", "RENDER"]
    assert _asset_variants("BTC") == ["BTC"]
    assert _asset_variants("POL") == ["POL", "MATIC"]
    assert _asset_variants("G") == ["G", "GAL"]


def test_search_terms_include_full_pair_variants() -> None:
    terms = _search_terms(_asset_variants("RENDER"), [Quote.USDT, Quote.USD])

    assert "RENDER" in terms
    assert "RNDR" in terms
    assert "RENDERUSDT" in terms
    assert "RNDRUSDT" in terms


def test_direct_crypto_markets_include_alias_symbols() -> None:
    markets = _direct_crypto_markets(
        "RENDER",
        base_variants=_asset_variants("RENDER"),
        quotes=[Quote.USDT],
    )
    symbols = {market.tradingview_symbol for market in markets}

    assert "BINANCE:RENDERUSDT" in symbols
    assert "BINANCE:RNDRUSDT" in symbols


def test_symbols_to_markets_accepts_alias_base() -> None:
    markets = _symbols_to_markets(
        [
            {
                "exchange": "BINANCE",
                "symbol": "RNDRUSDT",
                "base_currency_code": "RNDR",
                "currency_code": "USDT",
            }
        ],
        base="RENDER",
        base_variants=_asset_variants("RENDER"),
        quotes=[Quote.USDT],
    )

    assert len(markets) == 1
    assert markets[0].base == "RENDER"
    assert markets[0].tradingview_symbol == "BINANCE:RNDRUSDT"


def test_symbols_to_markets_accepts_full_pair_match() -> None:
    markets = _symbols_to_markets(
        [
            {
                "exchange": "BINANCE",
                "symbol": "RENDERUSDT",
                "currency_code": "USDT",
            }
        ],
        base="RENDER",
        base_variants=_asset_variants("RENDER"),
        quotes=[Quote.USDT],
    )

    assert len(markets) == 1
    assert markets[0].tradingview_symbol == "BINANCE:RENDERUSDT"


def test_quote_aliases_include_tether_us_names() -> None:
    aliases = _quote_aliases([Quote.USDT])

    assert aliases["USDT"] is Quote.USDT
    assert aliases["TETHERUS"] is Quote.USDT
    assert aliases["TETHERUSD"] is Quote.USDT


def test_symbols_to_markets_accepts_tether_us_description() -> None:
    markets = _symbols_to_markets(
        [
            {
                "exchange": "BINANCE",
                "symbol": "RENDERUSDT",
                "description": "RENDER / TetherUS",
            }
        ],
        base="RENDER",
        base_variants=_asset_variants("RENDER"),
        quotes=[Quote.USDT],
    )

    assert len(markets) == 1
    assert markets[0].quote is Quote.USDT
    assert markets[0].tradingview_symbol == "BINANCE:RENDERUSDT"


def test_direct_crypto_markets_include_short_gate_token_symbol() -> None:
    markets = _direct_crypto_markets("GT", quotes=[Quote.USDT])
    symbols = {market.tradingview_symbol for market in markets}

    assert "GATEIO:GTUSDT" in symbols
