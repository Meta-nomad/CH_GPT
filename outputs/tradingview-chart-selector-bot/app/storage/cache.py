from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.models import AnalysisResult, ChartMetrics, ChartScore, MarketSymbol, Quote


class AnalysisCache:
    def __init__(self, db_path: str, ttl_seconds: int) -> None:
        self.db_path = Path(db_path)
        self.ttl_seconds = ttl_seconds
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get(self, query: str) -> AnalysisResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "select payload, created_at from analysis_cache where query = ?",
                (query,),
            ).fetchone()
        if not row:
            return None

        payload, created_at = row
        created = datetime.fromisoformat(created_at)
        age = (datetime.now(timezone.utc) - created).total_seconds()
        if age > self.ttl_seconds:
            return None
        return _result_from_dict(json.loads(payload))

    def set(self, query: str, result: AnalysisResult) -> None:
        payload = json.dumps(_result_to_dict(result), ensure_ascii=False)
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as connection:
            connection.execute(
                """
                insert into analysis_cache(query, payload, created_at)
                values (?, ?, ?)
                on conflict(query) do update set payload = excluded.payload, created_at = excluded.created_at
                """,
                (query, payload, now),
            )

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                create table if not exists analysis_cache (
                    query text primary key,
                    payload text not null,
                    created_at text not null
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)


def _result_to_dict(result: AnalysisResult) -> dict[str, Any]:
    data = asdict(result)
    data["generated_at"] = result.generated_at.isoformat()
    for item in data["ranked"]:
        item["symbol"]["quote"] = item["symbol"]["quote"].value
        metrics = item["metrics"]
        for key in ("first_candle_at", "last_candle_at"):
            if metrics[key]:
                metrics[key] = metrics[key].isoformat()
    return data


def _result_from_dict(data: dict[str, Any]) -> AnalysisResult:
    ranked: list[ChartScore] = []
    for item in data["ranked"]:
        symbol_data = item["symbol"]
        metrics_data = item["metrics"]
        symbol = MarketSymbol(
            exchange_id=symbol_data["exchange_id"],
            exchange_name=symbol_data["exchange_name"],
            base=symbol_data["base"],
            quote=Quote(symbol_data["quote"]),
            market_symbol=symbol_data["market_symbol"],
            tradingview_exchange=symbol_data["tradingview_exchange"],
        )
        metrics = ChartMetrics(
            history_days=metrics_data["history_days"],
            first_candle_at=_parse_dt(metrics_data["first_candle_at"]),
            last_candle_at=_parse_dt(metrics_data["last_candle_at"]),
            expected_candles=metrics_data["expected_candles"],
            actual_candles=metrics_data["actual_candles"],
            gap_count=metrics_data["gap_count"],
            flat_candle_ratio=metrics_data["flat_candle_ratio"],
            zero_volume_ratio=metrics_data["zero_volume_ratio"],
            spike_count=metrics_data["spike_count"],
            average_volume=metrics_data["average_volume"],
        )
        ranked.append(
            ChartScore(
                symbol=symbol,
                metrics=metrics,
                score=item["score"],
                reasons=item["reasons"],
                penalties=item["penalties"],
            )
        )

    return AnalysisResult(
        query=data["query"],
        generated_at=datetime.fromisoformat(data["generated_at"]),
        ranked=ranked,
        mexc_futures_available=data.get("mexc_futures_available"),
    )


def _parse_dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None
