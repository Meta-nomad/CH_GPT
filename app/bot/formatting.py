from __future__ import annotations

from app.core.models import AnalysisResult, ChartScore


def format_analysis(result: AnalysisResult) -> str:
    if not result.best:
        return (
            "Не нашел подходящих графиков.\n\n"
            "Попробуй тикер без пары, например BTC, ETH, SOL или SUI."
        )

    best = result.best
    alternatives = result.ranked[1:4]
    lines = [
        "🏆 Лучший график для TradingView",
        "",
        f"<b>{best.symbol.tradingview_symbol}</b>",
        f"Рейтинг: <b>{best.score:.2f}</b>",
        "",
        _format_metrics(best),
        "",
        "Почему он выбран:",
        *[f"✓ {reason}" for reason in best.reasons[:4]],
    ]

    if best.penalties:
        lines.extend(["", "Что снижает оценку:", *[f"• {penalty}" for penalty in best.penalties[:3]]])

    if alternatives:
        lines.extend(["", "Альтернативы:"])
        for index, item in enumerate(alternatives, start=2):
            lines.append(f"{index}. {item.symbol.tradingview_symbol} - {item.score:.2f}")

    return "\n".join(lines)


def format_compare(result: AnalysisResult) -> str:
    if not result.ranked:
        return "Нет данных для сравнения."

    lines = [f"Сравнение графиков для <b>{result.query}</b>:", ""]
    for index, item in enumerate(result.ranked[:10], start=1):
        defect_mark = "чистый" if not item.metrics.has_defects else "есть дефекты"
        lines.append(
            f"{index}. <b>{item.symbol.tradingview_symbol}</b> - {item.score:.2f}, "
            f"{_history_label(item)}, {defect_mark}"
        )
    return "\n".join(lines)


def _format_metrics(item: ChartScore) -> str:
    metrics = item.metrics
    first_seen = metrics.first_candle_at.date().isoformat() if metrics.first_candle_at else "нет данных"
    return "\n".join(
        [
            f"История: {_history_label(item)}",
            f"Первая свеча: {first_seen}",
            f"Разрывы: {metrics.gap_count}",
            f"Плоские свечи: {metrics.flat_candle_ratio:.2%}",
            f"Нулевой объем: {metrics.zero_volume_ratio:.2%}",
            f"Подозрительные скачки: {metrics.spike_count}",
        ]
    )


def _history_label(item: ChartScore) -> str:
    days = item.metrics.history_days
    if days >= 365:
        return f"{days / 365:.1f} лет"
    return f"{days:.0f} дней"
