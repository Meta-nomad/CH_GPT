from datetime import datetime, timedelta, timezone

from app.core.models import Candle, MarketSymbol, Quote
from app.core.scoring import calculate_metrics, score_chart


def make_candles(count: int, *, gap_at: int | None = None) -> list[Candle]:
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    candles = []
    offset = 0
    for index in range(count):
        if gap_at is not None and index == gap_at:
            offset += 2
        price = 100 + index * 0.1
        candles.append(
            Candle(
                timestamp=start + timedelta(hours=index + offset),
                open=price,
                high=price + 1,
                low=price - 1,
                close=price + 0.2,
                volume=10_000,
            )
        )
    return candles


def test_metrics_detect_gaps() -> None:
    metrics = calculate_metrics(make_candles(10, gap_at=5))

    assert metrics.gap_count == 2
    assert metrics.actual_candles == 10
    assert metrics.expected_candles == 12


def test_clean_long_usdt_chart_scores_higher_than_broken_usd_chart() -> None:
    usdt_symbol = MarketSymbol("binance", "Binance", "SUI", Quote.USDT, "SUI/USDT", "BINANCE")
    usd_symbol = MarketSymbol("coinbase", "Coinbase", "SUI", Quote.USD, "SUI/USD", "COINBASE")

    clean_metrics = calculate_metrics(make_candles(1000))
    broken_metrics = calculate_metrics(make_candles(500, gap_at=100))

    clean = score_chart(usdt_symbol, clean_metrics, query_birth_year=2023)
    broken = score_chart(usd_symbol, broken_metrics, query_birth_year=2023)

    assert clean.score > broken.score
    assert "ликвидная пара к USDT" in clean.reasons


def test_history_start_can_extend_beyond_quality_window() -> None:
    candles = make_candles(24)
    history_start = candles[0].timestamp - timedelta(days=365)

    metrics = calculate_metrics(candles, history_start_at=history_start)

    assert metrics.history_days > 365
    assert metrics.gap_count == 0
