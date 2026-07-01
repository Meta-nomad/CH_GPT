from datetime import datetime, timedelta, timezone

from app.core.analyzer import ChartAnalyzer, MAX_SCORE_MARKETS, _select_score_candidates, normalize_asset
from app.core.models import Candle, MarketSymbol, Quote
from app.providers.base import ExchangeProvider


class MemoryCache:
    def __init__(self) -> None:
        self.value = None
        self.key = None

    def get(self, key):
        return self.value if key == self.key else None

    def set(self, key, result):
        self.key = key
        self.value = result


class FakeMexcFuturesChecker:
    async def has_futures_market(self, base: str) -> bool | None:
        return base == "SUI"


class StaticProvider(ExchangeProvider):
    exchange_id = "static"
    exchange_name = "Static"
    tradingview_exchange = "STATIC"

    def __init__(self, markets: list[MarketSymbol]) -> None:
        self._markets = markets

    async def find_markets(self, base: str, quotes: list[Quote]) -> list[MarketSymbol]:
        return self._markets

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        raise AssertionError("Exchange candles must not be used for TradingView ranking")


class FakeTradingViewSource:
    def __init__(self, earliest_by_exchange: dict[str, datetime], *, empty: set[str] | None = None) -> None:
        self.earliest_by_exchange = earliest_by_exchange
        self.empty = empty or set()

    async def fetch_hourly_candles(self, market: MarketSymbol, *, limit: int) -> list[Candle]:
        if market.tradingview_exchange in self.empty:
            return []
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        return [
            Candle(
                timestamp=start + timedelta(hours=index),
                open=1,
                high=2,
                low=0.5,
                close=1.5,
                volume=10_000 if market.tradingview_exchange != "MEXC" else 20_000,
            )
            for index in range(limit)
        ]

    async def find_earliest_history_candle(self, market: MarketSymbol) -> Candle | None:
        timestamp = self.earliest_by_exchange[market.tradingview_exchange]
        return Candle(timestamp, 1, 2, 0.5, 1.5, 10_000)


def market(exchange: str, quote: Quote, base: str = "SUI") -> MarketSymbol:
    return MarketSymbol(exchange.lower(), exchange.title(), base, quote, f"{base}/{quote.value}", exchange)


def test_normalize_asset() -> None:
    assert normalize_asset("BINANCE:btcusdt") == "BTC"
    assert normalize_asset(" eth ") == "ETH"


async def test_analyzer_ranks_tradingview_markets_and_checks_mexc() -> None:
    markets = [market("MEXC", Quote.USDT), market("COINBASE", Quote.USD)]
    tv = FakeTradingViewSource(
        {
            "MEXC": datetime(2023, 1, 1, tzinfo=timezone.utc),
            "COINBASE": datetime(2023, 1, 1, tzinfo=timezone.utc),
        }
    )
    analyzer = ChartAnalyzer(
        [StaticProvider(markets)],
        MemoryCache(),
        max_candles=24,
        mexc_futures_checker=FakeMexcFuturesChecker(),
        tradingview_client=tv,
    )

    result = await analyzer.analyze("SUI")

    assert result.best is not None
    assert result.best.symbol.tradingview_symbol == "MEXC:SUIUSDT"
    assert len(result.ranked) == 2
    assert result.mexc_futures_available is True


async def test_post_usdt_asset_prefers_usable_usdt_over_usd() -> None:
    markets = [market("COINBASE", Quote.USD, "ATOM"), market("MEXC", Quote.USDT, "ATOM")]
    tv = FakeTradingViewSource(
        {
            "COINBASE": datetime(2019, 3, 1, tzinfo=timezone.utc),
            "MEXC": datetime(2019, 3, 1, tzinfo=timezone.utc),
        }
    )
    analyzer = ChartAnalyzer([StaticProvider(markets)], MemoryCache(), max_candles=24, tradingview_client=tv)

    result = await analyzer.analyze("ATOM")

    assert result.best is not None
    assert result.best.symbol.quote is Quote.USDT


async def test_pre_usdt_asset_can_choose_longer_usd_history() -> None:
    markets = [market("COINBASE", Quote.USD, "BTC"), market("MEXC", Quote.USDT, "BTC")]
    tv = FakeTradingViewSource(
        {
            "COINBASE": datetime(2014, 1, 1, tzinfo=timezone.utc),
            "MEXC": datetime(2017, 1, 1, tzinfo=timezone.utc),
        }
    )
    analyzer = ChartAnalyzer([StaticProvider(markets)], MemoryCache(), max_candles=24, tradingview_client=tv)

    result = await analyzer.analyze("BTC")

    assert result.best is not None
    assert result.best.symbol.quote is Quote.USD


async def test_longer_usdt_history_wins_between_usdt_markets() -> None:
    markets = [market("KUCOIN", Quote.USDT, "KAS"), market("GATEIO", Quote.USDT, "KAS")]
    tv = FakeTradingViewSource(
        {
            "KUCOIN": datetime(2026, 5, 1, tzinfo=timezone.utc),
            "GATEIO": datetime(2026, 3, 1, tzinfo=timezone.utc),
        }
    )
    analyzer = ChartAnalyzer([StaticProvider(markets)], MemoryCache(), max_candles=24, tradingview_client=tv)

    result = await analyzer.analyze("KAS")

    assert result.best is not None
    assert result.best.symbol.tradingview_exchange == "GATEIO"


async def test_tradingview_empty_candidate_is_excluded() -> None:
    markets = [market("MEXC", Quote.USDT, "KAIA"), market("BITGET", Quote.USDT, "KAIA")]
    tv = FakeTradingViewSource(
        {
            "MEXC": datetime(2019, 9, 1, tzinfo=timezone.utc),
            "BITGET": datetime(2025, 2, 1, tzinfo=timezone.utc),
        },
        empty={"MEXC"},
    )
    analyzer = ChartAnalyzer([StaticProvider(markets)], MemoryCache(), max_candles=24, tradingview_client=tv)

    result = await analyzer.analyze("KAIA")

    assert result.best is not None
    assert result.best.symbol.tradingview_exchange == "BITGET"
    assert all(item.symbol.tradingview_exchange != "MEXC" for item in result.ranked)


async def test_no_tradingview_candles_returns_no_ranked_results() -> None:
    markets = [market("MEXC", Quote.USDT, "STABLE")]
    tv = FakeTradingViewSource({"MEXC": datetime(2025, 1, 1, tzinfo=timezone.utc)}, empty={"MEXC"})
    analyzer = ChartAnalyzer([StaticProvider(markets)], MemoryCache(), max_candles=24, tradingview_client=tv)

    result = await analyzer.analyze("STABLE")

    assert result.best is None
    assert result.ranked == []


async def test_cache_key_uses_current_version() -> None:
    markets = [market("MEXC", Quote.USDT, "SUI")]
    tv = FakeTradingViewSource({"MEXC": datetime(2023, 1, 1, tzinfo=timezone.utc)})
    cache = MemoryCache()
    analyzer = ChartAnalyzer([StaticProvider(markets)], cache, max_candles=10, tradingview_client=tv)

    await analyzer.analyze("SUI")

    assert cache.key is not None
    assert cache.key.startswith("tv-fast-candidates-v23:SUI")


def test_score_candidate_selection_limits_direct_markets_but_keeps_usd_history_venues() -> None:
    exchanges = [
        "BINANCE",
        "COINBASE",
        "KRAKEN",
        "BITSTAMP",
        "BITFINEX",
        "OKX",
        "BYBIT",
        "BITGET",
        "MEXC",
        "GATEIO",
        "KUCOIN",
        "CRYPTOCOM",
        "GEMINI",
        "HTX",
        "WHITEBIT",
    ]
    markets = [market(exchange, quote, "BTC") for exchange in exchanges for quote in (Quote.USDT, Quote.USD)]

    selected = _select_score_candidates(markets)

    assert len(selected) == MAX_SCORE_MARKETS
    assert market("COINBASE", Quote.USD, "BTC") in selected
    assert market("KRAKEN", Quote.USD, "BTC") in selected
