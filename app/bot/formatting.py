from __future__ import annotations

from app.core.models import AnalysisResult, ChartScore, Quote

QUOTE_POLICY_YEAR = 2015


def format_analysis(result: AnalysisResult) -> str:
    if not result.best:
        return (
            f"Не нашел USD/USDT-графики TradingView для <b>{result.query}</b>.\n\n"
            "Если это крипто-токен, TradingView не отдал свечи по популярным биржам. "
            "Если это акция, например NASDAQ:MSTR, она не подходит под текущую задачу."
        )

    best = result.best
    alternatives = result.ranked[1:4]
    lines = [
        "🏆 Лучший график для TradingView",
        "",
        f"<b>{best.symbol.tradingview_symbol}</b>",
        f"Оценка качества: <b>{best.score:.2f}</b>",
        "",
        _format_mexc_futures(result.mexc_futures_available),
        "",
        _format_metrics(best),
        "",
        "Почему он выбран:",
        *[f"✓ {reason}" for reason in best.reasons[:4]],
        *_format_selection_detail(result),
    ]

    if result.mexc_futures_available is False:
        lines.extend(["", "⚠️ Предупреждение: монета не найдена на фьючерсах MEXC."])

    if best.penalties:
        lines.extend(["", "Что снижает оценку:", *[f"• {penalty}" for penalty in best.penalties[:3]]])

    if alternatives:
        lines.extend(["", "Альтернативы:"])
        for index, item in enumerate(alternatives, start=2):
            lines.append(
                f"{index}. {item.symbol.tradingview_symbol} - {item.score:.2f}, "
                f"{_history_label(item)}, первая {_first_seen_label(item)}, объем {_volume_label(item)}"
            )

    return "\n".join(lines)


def format_compare(result: AnalysisResult) -> str:
    if not result.ranked:
        return "Нет данных для сравнения."

    lines = [
        f"Сравнение графиков для <b>{result.query}</b>:",
        _format_mexc_futures(result.mexc_futures_available),
    ]
    if result.mexc_futures_available is False:
        lines.append("⚠️ Нет фьючерсов на MEXC.")
    lines.append("")

    for index, item in enumerate(result.ranked[:10], start=1):
        defect_mark = "чистый" if not item.metrics.has_defects else "есть дефекты"
        lines.append(
            f"{index}. <b>{item.symbol.tradingview_symbol}</b> - {item.score:.2f}, "
            f"{_history_label(item)}, первая {_first_seen_label(item)}, "
            f"объем {_volume_label(item)}, {defect_mark}"
        )
    return "\n".join(lines)


def _format_metrics(item: ChartScore) -> str:
    metrics = item.metrics
    return "\n".join(
        [
            f"История: {_history_label(item)}",
            f"Первая свеча TradingView: {_first_seen_label(item)}",
            f"Разрывы: {metrics.gap_count}",
            f"Плоские свечи: {metrics.flat_candle_ratio:.2%}",
            f"Нулевой объем: {metrics.zero_volume_ratio:.2%}",
            f"Подозрительные скачки: {metrics.spike_count}",
            f"Средний часовой объем: {_volume_label(item)}",
        ]
    )


def _format_mexc_futures(value: bool | None) -> str:
    if value is True:
        return "Фьючерсы MEXC: Да"
    if value is False:
        return "Фьючерсы MEXC: Нет"
    return "Фьючерсы MEXC: не удалось проверить"


def _format_selection_detail(result: AnalysisResult) -> list[str]:
    if result.best is None or len(result.ranked) < 2:
        return []
    best = result.best
    asset_year = _asset_birth_year(result)
    if (
        asset_year is None or asset_year >= QUOTE_POLICY_YEAR
    ) and best.symbol.quote is Quote.USDT and any(item.symbol.quote is Quote.USD for item in result.ranked[1:]):
        return ["✓ монета появилась после USDT, поэтому нормальный USDT-график выше USD"]

    second = result.ranked[1]
    same_history = abs(best.metrics.history_days - second.metrics.history_days) < 0.5
    same_quality = (
        best.metrics.gap_count == second.metrics.gap_count
        and abs(best.metrics.flat_candle_ratio - second.metrics.flat_candle_ratio) < 0.0001
        and abs(best.metrics.zero_volume_ratio - second.metrics.zero_volume_ratio) < 0.0001
        and best.metrics.spike_count == second.metrics.spike_count
    )
    if same_history and same_quality:
        if best.metrics.average_volume > second.metrics.average_volume * 1.05:
            return ["✓ при равной истории и чистоте выше средний часовой объем TradingView"]
        return ["✓ история и качество почти равны с альтернативами; выбран по техническому приоритету"]
    return []


def _asset_birth_year(result: AnalysisResult) -> int | None:
    dates = [item.metrics.first_candle_at for item in result.ranked if item.metrics.first_candle_at]
    return min(dates).year if dates else None


def _history_label(item: ChartScore) -> str:
    if item.metrics.first_candle_at is None:
        return "не подтверждена TradingView"
    days = item.metrics.history_days
    if days >= 365:
        return f"{days / 365:.1f} лет"
    return f"{days:.0f} дней"


def _first_seen_label(item: ChartScore) -> str:
    return item.metrics.first_candle_at.date().isoformat() if item.metrics.first_candle_at else "не подтверждена"


def _volume_label(item: ChartScore) -> str:
    value = item.metrics.average_volume
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}K"
    return f"{value:.0f}"
