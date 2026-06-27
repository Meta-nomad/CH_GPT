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
    analyzer = ChartAnalyzer([FakeProvider()], MemoryCache(), max_candles=100)

    result = await analyzer.analyze("SUI")

    assert result.best is not None
    assert result.best.symbol.tradingview_symbol == "FAKE:SUIUSDT"
    assert len(result.ranked) == 2
