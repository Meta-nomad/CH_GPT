from __future__ import annotations

import argparse
import asyncio
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.models import MarketSymbol, Quote
from app.providers.tradingview import TradingViewClient


def parse_symbol(value: str) -> MarketSymbol:
    exchange, raw_pair = value.upper().split(":", maxsplit=1)
    if raw_pair.endswith("USDT"):
        base = raw_pair[:-4]
        quote = Quote.USDT
        market_symbol = f"{base}/USDT"
    elif raw_pair.endswith("USD"):
        base = raw_pair[:-3]
        quote = Quote.USD
        market_symbol = f"{base}/USD"
    else:
        base = raw_pair
        quote = Quote.USDT
        market_symbol = raw_pair
    return MarketSymbol(
        exchange_id=exchange.lower(),
        exchange_name=exchange,
        base=base,
        quote=quote,
        market_symbol=market_symbol,
        tradingview_exchange=exchange,
    )


async def main() -> None:
    parser = argparse.ArgumentParser(description="Probe TradingView candles for exact symbols.")
    parser.add_argument("symbols", nargs="+", help="Examples: BITGET:KAIAUSDT MEXC:KAIAUSDT")
    parser.add_argument("--interval", default="1D", help="TradingView interval: 60, 1D, 1W, 1M")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()

    client = TradingViewClient()
    for symbol in args.symbols:
        market = parse_symbol(symbol)
        probe = await client.probe(market, interval=args.interval, limit=args.limit)
        if probe.ok:
            first = probe.first_candle_at.date().isoformat() if probe.first_candle_at else "?"
            print(f"{probe.symbol}: OK candles={probe.candles} first={first} method={probe.strategy}")
        else:
            print(f"{probe.symbol}: FAIL error={probe.error}")


if __name__ == "__main__":
    asyncio.run(main())
