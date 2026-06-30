from datetime import datetime, timezone

from app.providers.ccxt_provider import _extract_listing_timestamp


def test_extract_listing_timestamp_from_market_info() -> None:
    market = {"info": {"onlineTime": "1583020800000"}}

    timestamp = _extract_listing_timestamp(market)

    assert timestamp == int(datetime(2020, 3, 1, tzinfo=timezone.utc).timestamp() * 1000)


def test_extract_listing_timestamp_accepts_seconds() -> None:
    market = {"created": 1583020800}

    timestamp = _extract_listing_timestamp(market)

    assert timestamp == 1583020800000
