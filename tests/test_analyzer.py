from datetime import datetime, timedelta, timezone

from app.core.analyzer import ChartAnalyzer, normalize_asset
from app.core.models import Candle, MarketSymbol, Quote
from app.providers.base import ExchangeProvider


class MemoryCache:
    def __init__(self) -> None:
        self.value = None

    def get(self, query):
        return self.value

    def set(self, query, result):
        self.value = result


class FakeMexcFuturesChecker:
    async def has_futures_market(self, base: str) -> bool | None:
        return base == "SUI"


class FakeProvider(ExchangeProvider):
    exchange_id = "fake"
    exchange_name = "Fake"
    tradingview_exchange = "FAKE"

    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        return [
            MarketSymbol("fake", "Fake", base, Quote.USDT, f"{base}/USDT", "FAKE"),
            MarketSymbol("fake", "Fake", base, Quote.USD, f"{base}/USD", "FAKE"),
        ]

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        start = datetime(2021, 1, 1, tzinfo=timezone.utc)
        candles = []
        count = limit if market.quote is Quote.USDT else limit // 2
        for index in range(count):
            price = 10 + index * 0.01
            candles.append(
                Candle(
                    timestamp=start + timedelta(hours=index),
                    open=price,
                    high=price + 0.1,
                    low=price - 0.1,
                    close=price,
                    volume=10_000,
                )
            )
        return candles


def test_normalize_asset() -> None:
    assert normalize_asset("BINANCE:btcusdt") == "BTC"
    assert normalize_asset(" eth ") == "ETH"


async def test_analyzer_ranks_markets() -> None:
    analyzer = ChartAnalyzer(
        [FakeProvider()],
        MemoryCache(),
        max_candles=100,
        mexc_futures_checker=FakeMexcFuturesChecker(),
    )

    result = await analyzer.analyze("SUI")

    assert result.best is not None
    assert result.best.symbol.tradingview_symbol == "FAKE:SUIUSDT"
    assert len(result.ranked) == 2
    assert result.mexc_futures_available is True


class HistoryPriorityProvider(ExchangeProvider):
    exchange_id = "history"
    exchange_name = "History"
    tradingview_exchange = "HISTORY"

    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        return [
            MarketSymbol("history", "History", base, Quote.USDT, f"{base}/USDT:SHORT", "KUCOIN"),
            MarketSymbol("history", "History", base, Quote.USDT, f"{base}/USDT:LONG", "GATEIO"),
        ]

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        start = datetime(2026, 5, 1, tzinfo=timezone.utc)
        candles = []
        for index in range(24):
            price = 10 + index * 0.01
            candles.append(
                Candle(
                    timestamp=start + timedelta(hours=index),
                    open=price,
                    high=price + 0.1,
                    low=price - 0.1,
                    close=price,
                    volume=10_000,
                )
            )
        return candles

    async def find_earliest_history_candle(self, market: MarketSymbol) -> Candle | None:
        if market.tradingview_exchange == "GATEIO":
            timestamp = datetime(2026, 3, 1, tzinfo=timezone.utc)
        else:
            timestamp = datetime(2026, 5, 1, tzinfo=timezone.utc)
        return Candle(timestamp, 10, 10.1, 9.9, 10, 10_000)


async def test_analyzer_prefers_longer_history_before_score() -> None:
    analyzer = ChartAnalyzer([HistoryPriorityProvider()], MemoryCache(), max_candles=24)

    result = await analyzer.analyze("KAS")

    assert result.best is not None
    assert result.best.symbol.tradingview_exchange == "GATEIO"


async def test_cache_key_uses_current_history_version() -> None:
    cache = MemoryCache()
    analyzer = ChartAnalyzer([FakeProvider()], cache, max_candles=10)

    await analyzer.analyze("BTC")

    assert cache.value is not None


class KaiaOverrideProvider(ExchangeProvider):
    exchange_id = "kaia"
    exchange_name = "Kaia"
    tradingview_exchange = "KAIA"

    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        return [
            MarketSymbol("kaia", "Kaia", base, Quote.USDT, f"{base}/USDT", "MEXC"),
            MarketSymbol("kaia", "Kaia", base, Quote.USDT, f"{base}/USDT", "BITGET"),
        ]

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            Candle(
                timestamp=start + timedelta(hours=index),
                open=1,
                high=2,
                low=0.5,
                close=1.5,
                volume=10_000,
            )
            for index in range(24)
        ]

    async def find_earliest_history_candle(self, market: MarketSymbol) -> Candle | None:
        return Candle(datetime(2019, 9, 1, tzinfo=timezone.utc), 1, 2, 0.5, 1.5, 10_000)


async def test_tradingview_override_wins_over_exchange_ohlcv_history() -> None:
    analyzer = ChartAnalyzer([KaiaOverrideProvider()], MemoryCache(), max_candles=24)

    result = await analyzer.analyze("KAIA")

    assert result.best is not None
    assert result.best.symbol.tradingview_exchange == "BITGET"
    assert result.best.metrics.first_candle_at == datetime(2025, 2, 1, tzinfo=timezone.utc)
