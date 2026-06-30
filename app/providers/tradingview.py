from __future__ import annotations

import json
import random
import re
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import websockets

from app.core.models import Candle, MarketSymbol
from app.providers.base import ProviderError

TRADINGVIEW_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
AUTH_TOKEN = "unauthorized_user_token"


@dataclass(frozen=True)
class TradingViewProbe:
    symbol: str
    interval: str
    ok: bool
    candles: int
    first_candle_at: datetime | None
    strategy: str | None
    error: str | None = None


class TradingViewClient:
    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        return await self.fetch_candles(market, interval="60", limit=limit)

    async def find_earliest_history_candle(self, market: MarketSymbol) -> Candle | None:
        candidates: list[Candle] = []
        for interval in ("1M", "1W", "1D"):
            candles = await self.fetch_candles(market, interval=interval, limit=5000)
            if candles:
                candidates.append(min(candles, key=lambda candle: candle.timestamp))
        if not candidates:
            return None
        return min(candidates, key=lambda candle: candle.timestamp)

    async def probe(self, market: MarketSymbol, *, interval: str = "1D", limit: int = 5) -> TradingViewProbe:
        try:
            candles, strategy = await self._fetch_candles_with_strategy(
                market,
                interval=interval,
                limit=limit,
            )
        except Exception as exc:
            return TradingViewProbe(
                symbol=market.tradingview_symbol,
                interval=interval,
                ok=False,
                candles=0,
                first_candle_at=None,
                strategy=None,
                error=str(exc),
            )
        return TradingViewProbe(
            symbol=market.tradingview_symbol,
            interval=interval,
            ok=bool(candles),
            candles=len(candles),
            first_candle_at=candles[0].timestamp if candles else None,
            strategy=strategy if candles else None,
            error=None if candles else "no candles",
        )

    async def fetch_candles(self, market: MarketSymbol, *, interval: str, limit: int) -> list[Candle]:
        candles, _strategy = await self._fetch_candles_with_strategy(
            market,
            interval=interval,
            limit=limit,
        )
        return candles

    async def _fetch_candles_with_strategy(
        self,
        market: MarketSymbol,
        *,
        interval: str,
        limit: int,
    ) -> tuple[list[Candle], str | None]:
        last_error: Exception | None = None
        for strategy_name, resolved_symbol in _resolve_symbol_payloads(market.tradingview_symbol):
            try:
                candles = await self._fetch_candles_once(
                    resolved_symbol=resolved_symbol,
                    interval=interval,
                    limit=limit,
                )
            except Exception as exc:
                last_error = exc
                continue
            if candles:
                return candles, strategy_name
        if last_error is not None:
            raise ProviderError(
                f"TradingView request failed for {market.tradingview_symbol} {interval}: {last_error}"
            ) from last_error
        return [], None

    async def _fetch_candles_once(
        self,
        *,
        resolved_symbol: str,
        interval: str,
        limit: int,
    ) -> list[Candle]:
        chart_session = _session_id("cs")
        async with websockets.connect(
            TRADINGVIEW_WS_URL,
            origin="https://www.tradingview.com",
            ping_interval=None,
            close_timeout=5,
        ) as websocket:
            await _send(websocket, "set_auth_token", [AUTH_TOKEN])
            await _send(websocket, "chart_create_session", [chart_session, ""])
            await _send(websocket, "resolve_symbol", [chart_session, "symbol_1", resolved_symbol])
            await _send(
                websocket,
                "create_series",
                [chart_session, "s1", "s1", "symbol_1", interval, limit],
            )
            return await _read_series(websocket, chart_session)

    async def close(self) -> None:
        return None


async def _send(websocket: Any, method: str, params: list[Any]) -> None:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    await websocket.send(f"~m~{len(payload)}~m~{payload}")


async def _read_series(websocket: Any, chart_session: str) -> list[Candle]:
    latest: list[Candle] = []
    for _ in range(200):
        raw = await websocket.recv()
        if raw.startswith("~h~"):
            await websocket.send(raw)
            continue
        for message in _decode_messages(raw):
            method = message.get("m")
            params = message.get("p", [])
            if method == "timescale_update" and params and params[0] == chart_session:
                series = (params[1] or {}).get("s1") or {}
                parsed = _parse_series(series)
                if parsed:
                    latest = parsed
            if method == "series_completed" and latest:
                return latest
            if method == "symbol_error":
                return []
    return latest


def _resolve_symbol_payloads(symbol: str) -> list[tuple[str, str]]:
    compact = json.dumps(
        {
            "symbol": symbol,
            "adjustment": "splits",
            "session": "regular",
        },
        separators=(",", ":"),
    )
    no_session = json.dumps(
        {
            "symbol": symbol,
            "adjustment": "splits",
        },
        separators=(",", ":"),
    )
    return [
        ("equals_json_session", f"={compact}"),
        ("equals_json", f"={no_session}"),
        ("json_session", compact),
        ("plain_symbol", symbol),
    ]


def _decode_messages(raw: str) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    for match in re.finditer(r"~m~(\d+)~m~", raw):
        start = match.end()
        length = int(match.group(1))
        payload = raw[start : start + length]
        if payload.startswith("~h~"):
            continue
        try:
            value = json.loads(payload)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            messages.append(value)
    return messages


def _parse_series(series: dict[str, Any]) -> list[Candle]:
    timestamps = series.get("t") or []
    opens = series.get("o") or []
    highs = series.get("h") or []
    lows = series.get("l") or []
    closes = series.get("c") or []
    volumes = series.get("v") or [0] * len(timestamps)

    candles: list[Candle] = []
    for timestamp, open_, high, low, close, volume in zip(
        timestamps,
        opens,
        highs,
        lows,
        closes,
        volumes,
        strict=False,
    ):
        candles.append(
            Candle(
                timestamp=datetime.fromtimestamp(float(timestamp), tz=timezone.utc),
                open=float(open_),
                high=float(high),
                low=float(low),
                close=float(close),
                volume=float(volume or 0),
            )
        )
    return sorted(candles, key=lambda candle: candle.timestamp)


def _session_id(prefix: str) -> str:
    suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
    return f"{prefix}_{suffix}"
