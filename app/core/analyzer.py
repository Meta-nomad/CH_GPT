from __future__ import annotations

import asyncio
import logging

from app.core.models import AnalysisResult, Candle, ChartScore, MarketSymbol, Quote, utc_now
from app.core.scoring import calculate_metrics, infer_birth_year_from_metrics, score_chart
from app.providers.base import ExchangeProvider, ProviderError
from app.storage.cache import AnalysisCache

logger = logging.getLogger(__name__)
CACHE_VERSION = "tv-mexc-systemic-v22"

QUALITY_GAP_LIMIT = 0.05
QUALITY_FLAT_LIMIT = 0.05
QUALITY_ZERO_VOLUME_LIMIT = 0.10
QUALITY_SPIKE_LIMIT = 10
SCORE_TIMEOUT_SECONDS = 35
MARKET_SEARCH_TIMEOUT_SECONDS = 12
TV_PROBE_TIMEOUT_SECONDS = 12
MEXC_USDT_POLICY_YEAR = 2015
MEXC_FAST_CHECK_TIMEOUT_SECONDS = 3


class ChartAnalyzer:
    def __init__(
        self,
        providers: list[ExchangeProvider],
        cache: AnalysisCache,
        *,
        mexc_futures_checker: object | None = None,
        tradingview_client: object | None = None,
        max_candles: int = 1000,
        quote_policy_year: int = MEXC_USDT_POLICY_YEAR,
    ) -> None:
        self.providers = providers
        self.cache = cache
        self.mexc_futures_checker = mexc_futures_checker
        self.tradingview_client = tradingview_client
        self.max_candles = max_candles
        self.quote_policy_year = quote_policy_year
        self._score_semaphore = asyncio.Semaphore(4)

    async def analyze(self, query: str, *, force_refresh: bool = False) -> AnalysisResult:
        normalized = normalize_asset(query)
        cache_key = f"{CACHE_VERSION}:{normalized}"
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        markets = await self._find_markets(normalized)
        scored = await self._score_markets(markets)
        ranked = self._rank_scores(scored)
        mexc_futures_available = await self._check_mexc_futures(normalized)
        result = AnalysisResult(
            query=normalized,
            generated_at=utc_now(),
            ranked=ranked,
            mexc_futures_available=mexc_futures_available,
        )
        self.cache.set(cache_key, result)
        return result

    def _rank_scores(self, scored: list[ChartScore]) -> list[ChartScore]:
        first_dates = [item.metrics.first_candle_at for item in scored if item.metrics.first_candle_at]
        asset_birth_year = min(first_dates).year if first_dates else None
        prefer_usdt = asset_birth_year is None or asset_birth_year >= self.quote_policy_year
        return sorted(
            scored,
            key=lambda item: _rank_key(item, prefer_usdt=prefer_usdt),
            reverse=True,
        )

    async def probe_tradingview(self, query: str) -> str:
        normalized = normalize_asset(query)
        if self.tradingview_client is None:
            return "TradingView-клиент не подключен."
        try:
            markets = await asyncio.wait_for(
                self._find_markets(normalized),
                timeout=MARKET_SEARCH_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            return f"Поиск TradingView для {normalized} занял слишком много времени."
        if not markets:
            return f"TradingView не нашел рынки USD/USDT для {normalized}."

        checked_markets = sorted(markets, key=_market_display_key)
        probes = await asyncio.gather(
            *[
                asyncio.wait_for(
                    self.tradingview_client.probe(market, interval="1D", limit=3),
                    timeout=TV_PROBE_TIMEOUT_SECONDS,
                )
                for market in checked_markets
            ],
            return_exceptions=True,
        )

        ok_count = 0
        lines = [f"TradingView test для {normalized}:", ""]
        for market, probe in zip(checked_markets, probes, strict=True):
            if isinstance(probe, Exception):
                lines.append(f"{market.tradingview_symbol}: нет ({_short_error(probe)})")
                continue
            if probe.ok:
                ok_count += 1
                first = probe.first_candle_at.date().isoformat() if probe.first_candle_at else "?"
                lines.append(
                    f"{probe.symbol}: да, свечей {probe.candles}, первая {first}, метод {probe.strategy}"
                )
            else:
                lines.append(f"{probe.symbol}: нет ({probe.error or 'no candles'})")
        lines.append("")
        lines.append(f"Проверено рынков: {len(checked_markets)}. Рабочих через TradingView: {ok_count}.")
        return "\n".join(lines)

    async def probe_mexc_futures(self, query: str) -> str:
        normalized = normalize_asset(query)
        symbol = f"{normalized}_USDT"
        checker = self.mexc_futures_checker
        diagnostic = getattr(checker, "diagnostic", None) if checker is not None else None
        if diagnostic is not None:
            try:
                data = await diagnostic(normalized, force_refresh=True)
                status = "Да" if data.get("available") else "Нет"
                details = ", ".join(f"{key}={value}" for key, value in data.items() if key != "available")
                return f"MEXC Futures для {normalized}: {status}\n{details}"
            except Exception as exc:
                logger.warning("MEXC diagnostic failed for %s: %s", normalized, exc)
        result = await self._check_mexc_futures(normalized)
        if result is True:
            status = "Да"
        elif result is False:
            status = "Нет"
        else:
            status = "не удалось проверить"
        return f"MEXC Futures для {normalized}: {status}\nПроверялся живой рынок: {symbol} ticker + стакан"

    async def _find_markets(self, asset: str) -> list[MarketSymbol]:
        tv_markets = await self._find_tradingview_markets(asset)
        if tv_markets:
            return tv_markets
        return await self._find_exchange_markets(asset)

    async def _find_tradingview_markets(self, asset: str) -> list[MarketSymbol]:
        if self.tradingview_client is None:
            return []
        search_markets = getattr(self.tradingview_client, "search_markets", None)
        if search_markets is None:
            return []
        try:
            return await asyncio.wait_for(
                search_markets(asset, quotes=[Quote.USDT, Quote.USD]),
                timeout=MARKET_SEARCH_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            logger.warning("TradingView symbol search failed for %s: %s", asset, exc)
            return []

    async def _find_exchange_markets(self, asset: str) -> list[MarketSymbol]:
        tasks = [provider.find_markets(asset, quotes=[Quote.USDT, Quote.USD]) for provider in self.providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        markets: list[MarketSymbol] = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Provider %s failed during market search: %s", provider.exchange_id, result)
                continue
            markets.extend(result)
        return sorted(_dedupe_markets(markets), key=_market_display_key)

    async def _score_markets(self, markets: list[MarketSymbol]) -> list[ChartScore]:
        unique_markets = _dedupe_markets(markets)
        tasks = [self._score_market_limited(market) for market in unique_markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored: list[ChartScore] = []
        for market, result in zip(unique_markets, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Failed to score %s: %s", market.tradingview_symbol, result)
                continue
            scored.append(result)
        return scored

    async def _score_market_limited(self, market: MarketSymbol) -> ChartScore:
        async with self._score_semaphore:
            return await asyncio.wait_for(self._score_market(market), timeout=SCORE_TIMEOUT_SECONDS)

    async def _score_market(self, market: MarketSymbol) -> ChartScore:
        candles, earliest = await self._fetch_tradingview_chart_data(market)
        metrics = calculate_metrics(
            candles,
            history_start_at=earliest.timestamp if earliest else None,
        )
        birth_year = infer_birth_year_from_metrics(metrics.first_candle_at)
        return score_chart(
            market,
            metrics,
            query_birth_year=birth_year,
            quote_policy_year=self.quote_policy_year,
        )

    async def _fetch_tradingview_chart_data(self, market: MarketSymbol) -> tuple[list[Candle], Candle | None]:
        if self.tradingview_client is None:
            raise ProviderError("TradingView client is not available")
        candles = await self.tradingview_client.fetch_hourly_candles(
            market,
            limit=self.max_candles,
        )
        if not candles:
            raise ProviderError(f"TradingView returned no candles for {market.tradingview_symbol}")
        earliest = await self.tradingview_client.find_earliest_history_candle(market)
        return candles, earliest

    async def _check_mexc_futures(self, asset: str) -> bool | None:
        if self.mexc_futures_checker is None:
            return None
        checker = self.mexc_futures_checker
        has_market = getattr(checker, "has_futures_market", None)
        if has_market is None:
            return None
        try:
            return await asyncio.wait_for(has_market(asset), timeout=MEXC_FAST_CHECK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("MEXC futures check timed out for %s", asset)
            return None
        except Exception as exc:
            logger.warning("MEXC futures check failed for %s: %s", asset, exc)
            return None


def normalize_asset(value: str) -> str:
    cleaned = value.strip().upper()
    if ":" in cleaned:
        cleaned = cleaned.split(":", maxsplit=1)[1]
    for quote in ("USDT", "USD"):
        if cleaned.endswith(quote):
            cleaned = cleaned[: -len(quote)]
    return "".join(ch for ch in cleaned if ch.isalnum())


def _rank_key(
    item: ChartScore,
    *,
    prefer_usdt: bool,
) -> tuple[int, int, int, int, int, float, float, float, float, float, int, float]:
    metrics = item.metrics
    expected = max(metrics.actual_candles + metrics.gap_count, 1)
    gap_ratio = metrics.gap_count / expected
    is_usable = int(_is_usable_quality(item))
    history_months = int(metrics.history_days // 30)
    usdt_priority = 1 if item.symbol.quote is Quote.USDT else 0
    quote_policy_priority = usdt_priority if prefer_usdt else 0
    return (
        is_usable,
        quote_policy_priority,
        history_months,
        item.symbol.match_priority,
        usdt_priority,
        item.score,
        metrics.average_volume,
        -gap_ratio,
        -metrics.flat_candle_ratio,
        -metrics.zero_volume_ratio,
        -metrics.spike_count,
        metrics.history_days,
    )


def _is_usable_quality(item: ChartScore) -> bool:
    metrics = item.metrics
    expected = max(metrics.actual_candles + metrics.gap_count, 1)
    gap_ratio = metrics.gap_count / expected
    return (
        gap_ratio <= QUALITY_GAP_LIMIT
        and metrics.flat_candle_ratio <= QUALITY_FLAT_LIMIT
        and metrics.zero_volume_ratio <= QUALITY_ZERO_VOLUME_LIMIT
        and metrics.spike_count <= QUALITY_SPIKE_LIMIT
    )


def _dedupe_markets(markets: list[MarketSymbol]) -> list[MarketSymbol]:
    seen: set[str] = set()
    unique: list[MarketSymbol] = []
    for market in markets:
        key = market.tradingview_symbol
        if key in seen:
            continue
        seen.add(key)
        unique.append(market)
    return unique


_EXCHANGE_DISPLAY_PRIORITY = {
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
}


def _market_display_key(market: MarketSymbol) -> tuple[int, int, str]:
    quote_priority = 0 if market.quote is Quote.USDT else 1
    exchange_priority = _EXCHANGE_DISPLAY_PRIORITY.get(market.tradingview_exchange, 99)
    return (quote_priority, exchange_priority, market.tradingview_symbol)


def _short_error(exc: Exception) -> str:
    if isinstance(exc, TimeoutError):
        return "timeout"
    text = str(exc).strip()
    if text:
        return text[:160]
    return exc.__class__.__name__
