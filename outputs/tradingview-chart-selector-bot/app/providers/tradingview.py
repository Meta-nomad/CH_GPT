from __future__ import annotations

import asyncio
import json
import random
import re
import string
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

import websockets

from app.core.models import Candle, MarketSymbol, Quote
from app.providers.base import ProviderError

TRADINGVIEW_WS_URL = "wss://data.tradingview.com/socket.io/websocket"
TRADINGVIEW_SEARCH_URL = "https://symbol-search.tradingview.com/symbol_search/v3/"
AUTH_TOKEN = "unauthorized_user_token"
CONNECT_TIMEOUT_SECONDS = 4
READ_TIMEOUT_SECONDS = 3
STRATEGY_TIMEOUT_SECONDS = 7
PROBE_TIMEOUT_SECONDS = 10
SEARCH_TIMEOUT_SECONDS = 8
MAX_READ_MESSAGES = 12

TV_EXCHANGE_NAMES = {
    "BINANCE": "Binance",
    "BYBIT": "Bybit",
    "COINBASE": "Coinbase",
    "KRAKEN": "Kraken",
    "BITSTAMP": "Bitstamp",
    "OKX": "OKX",
    "BITFINEX": "Bitfinex",
    "KUCOIN": "KuCoin",
    "MEXC": "MEXC",
    "GATEIO": "Gate.io",
    "BITGET": "Bitget",
    "CRYPTOCOM": "Crypto.com",
    "GEMINI": "Gemini",
    "HTX": "HTX",
    "BINGX": "BingX",
    "LBANK": "LBank",
    "BITMART": "BitMart",
    "POLONIEX": "Poloniex",
    "COINEX": "CoinEx",
    "ASCENDEX": "AscendEX",
    "PHEMEX": "Phemex",
    "WHITEBIT": "WhiteBIT",
    "BLOFIN": "BloFin",
}

TV_EXCHANGE_ID = {
    "BINANCE": "binance",
    "BYBIT": "bybit",
    "COINBASE": "coinbase",
    "KRAKEN": "kraken",
    "BITSTAMP": "bitstamp",
    "OKX": "okx",
    "BITFINEX": "bitfinex",
    "KUCOIN": "kucoin",
    "MEXC": "mexc",
    "GATEIO": "gateio",
    "BITGET": "bitget",
    "CRYPTOCOM": "cryptocom",
    "GEMINI": "gemini",
    "HTX": "htx",
    "BINGX": "bingx",
    "LBANK": "lbank",
    "BITMART": "bitmart",
    "POLONIEX": "poloniex",
    "COINEX": "coinex",
    "ASCENDEX": "ascendex",
    "PHEMEX": "phemex",
    "WHITEBIT": "whitebit",
    "BLOFIN": "blofin",
}


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
    async def search_markets(self, base: str, *, quotes: list[Quote]) -> list[MarketSymbol]:
        base_variants = _asset_variants(base)
        search_terms = _search_terms(base_variants, quotes)
        symbol_batches = await asyncio.gather(
            *[
                asyncio.wait_for(
                    asyncio.to_thread(_search_symbols_sync, term),
                    timeout=SEARCH_TIMEOUT_SECONDS,
                )
                for term in search_terms
            ],
            return_exceptions=True,
        )
        symbols: list[dict[str, Any]] = []
        for batch in symbol_batches:
            if isinstance(batch, Exception):
                continue
            symbols.extend(batch)
        found = _symbols_to_markets(symbols, base=base, base_variants=base_variants, quotes=quotes)
        direct = _direct_crypto_markets(base, base_variants=base_variants, quotes=quotes)
        return _merge_markets([*found, *direct])

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
            candles, strategy = await asyncio.wait_for(
                self._fetch_candles_with_strategy(market, interval=interval, limit=limit),
                timeout=PROBE_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            return TradingViewProbe(
                symbol=market.tradingview_symbol,
                interval=interval,
                ok=False,
                candles=0,
                first_candle_at=None,
                strategy=None,
                error=_format_exception(exc),
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
                candles = await asyncio.wait_for(
                    self._fetch_candles_once(
                        resolved_symbol=resolved_symbol,
                        interval=interval,
                        limit=limit,
                    ),
                    timeout=STRATEGY_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                last_error = exc
                continue
            if candles:
                return candles, strategy_name
        if last_error is not None:
            raise ProviderError(
                f"TradingView request failed for {market.tradingview_symbol} {interval}: {_format_exception(last_error)}"
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
        websocket = await asyncio.wait_for(
            websockets.connect(
                TRADINGVIEW_WS_URL,
                origin="https://www.tradingview.com",
                user_agent_header=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
                ping_interval=None,
                close_timeout=2,
                open_timeout=CONNECT_TIMEOUT_SECONDS,
            ),
            timeout=CONNECT_TIMEOUT_SECONDS,
        )
        try:
            await _send(websocket, "set_auth_token", [AUTH_TOKEN])
            await _send(websocket, "chart_create_session", [chart_session, ""])
            await _send(websocket, "resolve_symbol", [chart_session, "symbol_1", resolved_symbol])
            await _send(
                websocket,
                "create_series",
                [chart_session, "s1", "s1", "symbol_1", interval, limit],
            )
            return await _read_series(websocket, chart_session)
        finally:
            await websocket.close()

    async def close(self) -> None:
        return None


async def _send(websocket: Any, method: str, params: list[Any]) -> None:
    payload = json.dumps({"m": method, "p": params}, separators=(",", ":"))
    await websocket.send(f"~m~{len(payload)}~m~{payload}")


async def _read_series(websocket: Any, chart_session: str) -> list[Candle]:
    latest: list[Candle] = []
    for _ in range(MAX_READ_MESSAGES):
        raw = await asyncio.wait_for(websocket.recv(), timeout=READ_TIMEOUT_SECONDS)
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
                details = params[1] if len(params) > 1 else "symbol_error"
                raise ProviderError(f"symbol_error: {details}")
    return latest


def _search_symbols_sync(base: str) -> list[dict[str, Any]]:
    query = quote_plus(base.upper())
    url = (
        f"{TRADINGVIEW_SEARCH_URL}?text={query}&hl=1&exchange=&lang=en"
        "&search_type=crypto&domain=production"
    )
    request = Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            ),
            "Origin": "https://www.tradingview.com",
            "Referer": "https://www.tradingview.com/",
        },
    )
    with urlopen(request, timeout=SEARCH_TIMEOUT_SECONDS) as response:
        data = json.loads(response.read().decode("utf-8"))
    if isinstance(data, dict):
        symbols = data.get("symbols") or []
        return symbols if isinstance(symbols, list) else []
    return data if isinstance(data, list) else []


def _direct_crypto_markets(
    base: str,
    *,
    base_variants: list[str] | None = None,
    quotes: list[Quote],
) -> list[MarketSymbol]:
    wanted_base = _clean_asset(base)
    variants = base_variants or [wanted_base]
    markets: list[MarketSymbol] = []
    for exchange in _TV_DIRECT_EXCHANGES:
        for variant in variants:
            for quote in quotes:
                markets.append(
                    MarketSymbol(
                        exchange_id=TV_EXCHANGE_ID.get(exchange, exchange.lower()),
                        exchange_name=TV_EXCHANGE_NAMES[exchange],
                        base=wanted_base,
                        quote=quote,
                        market_symbol=f"{variant}/{quote.value}",
                        tradingview_exchange=exchange,
                        match_priority=3,
                    )
                )
    return markets


def _merge_markets(markets: list[MarketSymbol]) -> list[MarketSymbol]:
    seen: set[str] = set()
    merged: list[MarketSymbol] = []
    for market in markets:
        key = market.tradingview_symbol
        if key in seen:
            continue
        seen.add(key)
        merged.append(market)
    return sorted(merged, key=_market_sort_key)


def _symbols_to_markets(
    symbols: list[dict[str, Any]],
    *,
    base: str,
    base_variants: list[str] | None = None,
    quotes: list[Quote],
) -> list[MarketSymbol]:
    wanted_base = _clean_asset(base)
    wanted_bases = set(base_variants or [wanted_base])
    wanted_quotes = {quote.value for quote in quotes}
    quote_aliases = _quote_aliases(quotes)
    wanted_tickers = {
        f"{variant}{quote_alias}"
        for variant in wanted_bases
        for quote_alias in quote_aliases
    }
    seen: set[str] = set()
    markets: list[MarketSymbol] = []
    for item in symbols:
        exchange = _clean_exchange(item)
        if exchange not in TV_EXCHANGE_NAMES:
            continue
        symbol = _clean_symbol(item)
        if not symbol:
            continue
        quote = _detect_quote(item, symbol, wanted_quotes)
        if quote is None:
            continue
        symbol_base = _detect_base(item, symbol, quote.value)
        if not _is_symbol_ticker_match(symbol, wanted_bases, wanted_tickers):
            continue
        key = f"{exchange}:{symbol}"
        if key in seen:
            continue
        seen.add(key)
        markets.append(
            MarketSymbol(
                exchange_id=TV_EXCHANGE_ID.get(exchange, exchange.lower()),
                exchange_name=TV_EXCHANGE_NAMES[exchange],
                base=wanted_base,
                quote=quote,
                market_symbol=symbol,
                tradingview_exchange=exchange,
                match_priority=_symbol_match_priority(symbol, symbol_base, wanted_bases, wanted_tickers),
            )
        )
    return sorted(markets, key=_market_sort_key)


def _is_symbol_ticker_match(symbol: str, wanted_bases: set[str], wanted_tickers: set[str]) -> bool:
    if symbol in wanted_tickers:
        return True
    return any(symbol.startswith(base) for base in wanted_bases)


def _symbol_match_priority(
    symbol: str,
    symbol_base: str,
    wanted_bases: set[str],
    wanted_tickers: set[str],
) -> int:
    if symbol in wanted_tickers:
        return 3
    if symbol_base in wanted_bases and any(symbol.startswith(base) for base in wanted_bases):
        return 3
    if symbol_base in wanted_bases:
        return 2
    return 1


def _clean_exchange(item: dict[str, Any]) -> str:
    value = item.get("exchange") or item.get("prefix") or ""
    return re.sub(r"[^A-Z0-9]", "", str(value).upper())


def _clean_symbol(item: dict[str, Any]) -> str:
    symbol = item.get("symbol") or item.get("ticker") or ""
    return re.sub(r"[^A-Z0-9]", "", str(symbol).upper())


def _detect_quote(item: dict[str, Any], symbol: str, wanted_quotes: set[str]) -> Quote | None:
    quote_aliases = _quote_aliases([Quote(value) for value in wanted_quotes])
    candidates = [
        item.get("currency_code"),
        item.get("currency"),
        item.get("quote_currency"),
        item.get("description"),
        item.get("short_description"),
        item.get("full_name"),
    ]
    for candidate in candidates:
        value = _clean_asset(str(candidate or ""))
        quote = quote_aliases.get(value)
        if quote is not None:
            return quote
        for alias, quote in quote_aliases.items():
            if value.endswith(alias):
                return quote
    for alias, quote in sorted(quote_aliases.items(), key=lambda item: len(item[0]), reverse=True):
        if symbol.endswith(alias):
            return quote
    return None


def _detect_base(item: dict[str, Any], symbol: str, quote: str) -> str:
    candidates = [
        item.get("base_currency_code"),
        item.get("base_currency"),
        item.get("root"),
    ]
    for candidate in candidates:
        value = re.sub(r"[^A-Z0-9]", "", str(candidate or "").upper())
        if value:
            return value
    return symbol[: -len(quote)] if symbol.endswith(quote) else symbol


def _market_sort_key(market: MarketSymbol) -> tuple[int, int, str]:
    quote_priority = 0 if market.quote is Quote.USDT else 1
    exchange_priority = _TV_EXCHANGE_PRIORITY.get(market.tradingview_exchange, 99)
    return (quote_priority, exchange_priority, market.tradingview_symbol)


_TV_DIRECT_EXCHANGES = tuple(TV_EXCHANGE_NAMES)


_ASSET_ALIAS_GROUPS = (
    ("RENDER", "RNDR"),
    ("POL", "MATIC"),
    ("KAIA", "KLAY"),
    ("BEAM", "BEAMX"),
    ("G", "GAL"),
    ("XNO", "NANO"),
    ("BTT", "BTTOLD"),
    ("LUNC", "LUNA"),
)


def _clean_asset(value: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", value.upper())


def _asset_variants(base: str) -> list[str]:
    cleaned = _clean_asset(base)
    variants = [cleaned]
    for group in _ASSET_ALIAS_GROUPS:
        if cleaned in group:
            variants.extend(alias for alias in group if alias != cleaned)
            break
    return list(dict.fromkeys(variants))


def _search_terms(base_variants: list[str], quotes: list[Quote]) -> list[str]:
    terms = list(base_variants)
    terms.extend(f"{variant}{quote.value}" for variant in base_variants for quote in quotes)
    return list(dict.fromkeys(terms))


_QUOTE_ALIAS_GROUPS = {
    Quote.USDT: (
        "USDT",
        "TETHERUS",
        "TETHERUSDT",
        "TETHERUSD",
        "TETHER",
    ),
    Quote.USD: (
        "USD",
        "USDOLLAR",
        "US DOLLAR",
        "DOLLAR",
    ),
}


def _quote_aliases(quotes: list[Quote]) -> dict[str, Quote]:
    aliases: dict[str, Quote] = {}
    for quote in quotes:
        for alias in _QUOTE_ALIAS_GROUPS.get(quote, (quote.value,)):
            aliases[_clean_asset(alias)] = quote
    return aliases


_TV_EXCHANGE_PRIORITY = {
    "BITGET": 0,
    "MEXC": 1,
    "GATEIO": 2,
    "KUCOIN": 3,
    "OKX": 4,
    "BINANCE": 5,
    "BYBIT": 6,
    "KRAKEN": 7,
    "BITFINEX": 8,
    "COINBASE": 9,
    "HTX": 10,
    "BINGX": 11,
    "LBANK": 12,
    "BITMART": 13,
    "POLONIEX": 14,
    "COINEX": 15,
    "ASCENDEX": 16,
    "PHEMEX": 17,
    "WHITEBIT": 18,
    "BLOFIN": 19,
}


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
    row_candles = _parse_row_series(series)
    if row_candles:
        return row_candles

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


def _parse_row_series(series: dict[str, Any]) -> list[Candle]:
    rows = series.get("s") or []
    candles: list[Candle] = []
    if not isinstance(rows, list):
        return candles
    for row in rows:
        if not isinstance(row, dict):
            continue
        values = row.get("v") or []
        if len(values) < 5:
            continue
        try:
            timestamp = values[0]
            open_ = values[1]
            high = values[2]
            low = values[3]
            close = values[4]
            volume = values[5] if len(values) > 5 else 0
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
        except (TypeError, ValueError):
            continue
    return sorted(candles, key=lambda candle: candle.timestamp)


def _session_id(prefix: str) -> str:
    suffix = "".join(random.choice(string.ascii_lowercase) for _ in range(12))
    return f"{prefix}_{suffix}"


def _format_exception(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    text = str(exc).strip()
    if text:
        return text[:200]
    return exc.__class__.__name__
