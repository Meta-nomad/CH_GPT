from __future__ import annotations

import asyncio
import logging

from app.core.models import AnalysisResult, ChartScore, MarketSymbol, Quote, utc_now
from app.core.scoring import calculate_metrics, infer_birth_year_from_metrics, score_chart
from app.providers.base import ExchangeProvider, ProviderError
from app.storage.cache import AnalysisCache

logger = logging.getLogger(__name__)
CACHE_VERSION = "history-ohlcv-batch-v5"


class ChartAnalyzer:
    def __init__(
        self,
        providers: list[ExchangeProvider],
        cache: AnalysisCache,
        *,
        mexc_futures_checker: object | None = None,
        max_candles: int = 1000,
        quote_policy_year: int = 2015,
    ) -> None:
        self.providers = providers
        self.cache = cache
        self.mexc_futures_checker = mexc_futures_checker
        self.max_candles = max_candles
        self.quote_policy_year = quote_policy_year

    async def analyze(self, query: str, *, force_refresh: bool = False) -> AnalysisResult:
        normalized = normalize_asset(query)
        cache_key = f"{CACHE_VERSION}:{normalized}"
        if not force_refresh:
            cached = self.cache.get(cache_key)
            if cached:
                return cached

        markets = await self._find_markets(normalized)
        scored = await self._score_markets(markets)
        ranked = sorted(scored, key=_rank_key, reverse=True)
        mexc_futures_available = await self._check_mexc_futures(normalized)
        result = AnalysisResult(
            query=normalized,
            generated_at=utc_now(),
            ranked=ranked,
            mexc_futures_available=mexc_futures_available,
        )
        self.cache.set(cache_key, result)
        return result

    async def _find_markets(self, asset: str) -> list[MarketSymbol]:
        tasks = [provider.find_markets(asset, quotes=[Quote.USDT, Quote.USD]) for provider in self.providers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        markets: list[MarketSymbol] = []
        for provider, result in zip(self.providers, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Provider %s failed during market search: %s", provider.exchange_id, result)
                continue
            markets.extend(result)

        return markets

    async def _score_markets(self, markets: list[MarketSymbol]) -> list[ChartScore]:
        tasks = [self._score_market(market) for market in markets]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        scored: list[ChartScore] = []
        for market, result in zip(markets, results, strict=True):
            if isinstance(result, Exception):
                logger.warning("Failed to score %s: %s", market.tradingview_symbol, result)
                continue
            scored.append(result)

        return scored

    async def _score_market(self, market: MarketSymbol) -> ChartScore:
        provider = self._provider_for(market.exchange_id)
        if provider is None:
            raise ProviderError(f"No provider for {market.exchange_id}")

        candles = await provider.fetch_hourly_candles(market, limit=self.max_candles)
        earliest = await provider.find_earliest_history_candle(market)
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

    async def _check_mexc_futures(self, asset: str) -> bool | None:
        if self.mexc_futures_checker is None:
            return None
        checker = self.mexc_futures_checker
        has_market = getattr(checker, "has_futures_market", None)
        if has_market is None:
            return None
        try:
            return await has_market(asset)
        except Exception as exc:
            logger.warning("MEXC futures check failed for %s: %s", asset, exc)
            return None

    def _provider_for(self, exchange_id: str) -> ExchangeProvider | None:
        return next((provider for provider in self.providers if provider.exchange_id == exchange_id), None)


def normalize_asset(value: str) -> str:
    cleaned = value.strip().upper()
    if ":" in cleaned:
        cleaned = cleaned.split(":", maxsplit=1)[1]
    for quote in ("USDT", "USD"):
        if cleaned.endswith(quote):
            cleaned = cleaned[: -len(quote)]
    return "".join(ch for ch in cleaned if ch.isalnum())


def _rank_key(item: ChartScore) -> tuple[int, float, int, float]:
    metrics = item.metrics
    expected = max(metrics.actual_candles + metrics.gap_count, 1)
    gap_ratio = metrics.gap_count / expected
    is_usable = int(
        gap_ratio <= 0.05
        and metrics.flat_candle_ratio <= 0.05
        and metrics.zero_volume_ratio <= 0.10
        and metrics.spike_count <= 10
    )
    quote_priority = 1 if item.symbol.quote is Quote.USDT else 0
    return (is_usable, metrics.history_days, quote_priority, item.score)
