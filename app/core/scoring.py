from __future__ import annotations

from datetime import datetime
from statistics import median

from app.core.models import Candle, ChartMetrics, ChartScore, MarketSymbol, Quote

SECONDS_PER_HOUR = 3600


def calculate_metrics(candles: list[Candle], *, history_start_at: datetime | None = None) -> ChartMetrics:
    if not candles:
        return ChartMetrics(0, history_start_at, None, 0, 0, 0, 0, 0, 0, 0)

    ordered = sorted(candles, key=lambda candle: candle.timestamp)
    first = history_start_at or ordered[0].timestamp
    last = ordered[-1].timestamp
    history_days = max((last - first).total_seconds() / 86_400, 0)
    expected = int((last - first).total_seconds() // SECONDS_PER_HOUR) + 1

    gap_count = 0
    for previous, current in zip(ordered, ordered[1:], strict=False):
        diff_hours = int((current.timestamp - previous.timestamp).total_seconds() // SECONDS_PER_HOUR)
        if diff_hours > 1:
            gap_count += diff_hours - 1

    flat_count = 0
    zero_volume_count = 0
    closes: list[float] = []
    volumes: list[float] = []

    for candle in ordered:
        price_range = candle.high - candle.low
        epsilon = max(abs(candle.close) * 0.000001, 0.00000001)
        if price_range <= epsilon or candle.open == candle.high == candle.low == candle.close:
            flat_count += 1
        if candle.volume <= 0:
            zero_volume_count += 1
        closes.append(candle.close)
        volumes.append(max(candle.volume, 0))

    spike_count = _count_spikes(closes)
    actual = len(ordered)
    average_volume = sum(volumes) / actual if actual else 0

    return ChartMetrics(
        history_days=history_days,
        first_candle_at=first,
        last_candle_at=last,
        expected_candles=expected,
        actual_candles=actual,
        gap_count=gap_count,
        flat_candle_ratio=flat_count / actual,
        zero_volume_ratio=zero_volume_count / actual,
        spike_count=spike_count,
        average_volume=average_volume,
    )


def score_chart(
    symbol: MarketSymbol,
    metrics: ChartMetrics,
    *,
    query_birth_year: int | None = None,
    quote_policy_year: int = 2015,
) -> ChartScore:
    reasons: list[str] = []
    penalties: list[str] = []

    history_component = min(metrics.history_days / (365 * 9), 1) * 45
    integrity_component = max(0, 20 - metrics.gap_count * 0.8)
    liquidity_component = min(metrics.average_volume / 10_000, 1) * 15
    candle_quality_component = max(
        0,
        10
        - metrics.flat_candle_ratio * 500
        - metrics.zero_volume_ratio * 250
        - metrics.spike_count * 0.4,
    )
    quote_component = _quote_score(symbol.quote, query_birth_year, quote_policy_year)

    score = (
        history_component
        + integrity_component
        + liquidity_component
        + candle_quality_component
        + quote_component
    )

    if metrics.history_days >= 365 * 5:
        reasons.append("длинная история")
    elif metrics.history_days >= 365:
        reasons.append("история больше года")

    if symbol.quote is Quote.USDT:
        reasons.append("ликвидная пара к USDT")
    else:
        reasons.append("пара к USD")

    if metrics.gap_count == 0:
        reasons.append("нет разрывов на часовых свечах")
    else:
        penalties.append(f"разрывы: {metrics.gap_count}")

    if metrics.flat_candle_ratio <= 0.001:
        reasons.append("почти нет плоских свечей")
    else:
        penalties.append(f"плоские свечи: {metrics.flat_candle_ratio:.2%}")

    if metrics.zero_volume_ratio > 0.01:
        penalties.append(f"нулевой объем: {metrics.zero_volume_ratio:.2%}")

    if metrics.spike_count:
        penalties.append(f"подозрительные скачки: {metrics.spike_count}")

    return ChartScore(symbol=symbol, metrics=metrics, score=round(score, 2), reasons=reasons, penalties=penalties)


def _quote_score(quote: Quote, birth_year: int | None, quote_policy_year: int) -> float:
    if quote is Quote.USDT and (birth_year is None or birth_year >= quote_policy_year):
        return 10
    if quote is Quote.USD and birth_year is not None and birth_year < quote_policy_year:
        return 8
    if quote is Quote.USDT:
        return 6
    return 4


def _count_spikes(closes: list[float]) -> int:
    returns = []
    for previous, current in zip(closes, closes[1:], strict=False):
        if previous > 0:
            returns.append(abs(current / previous - 1))

    if len(returns) < 20:
        return 0

    typical = median(returns)
    threshold = max(typical * 20, 0.35)
    return sum(1 for value in returns if value > threshold)


def infer_birth_year_from_metrics(first_seen: datetime | None) -> int | None:
    return first_seen.year if first_seen else None
