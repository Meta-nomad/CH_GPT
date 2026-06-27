from app.providers.base import ExchangeProvider
from app.providers.ccxt_provider import CcxtExchangeProvider


DEFAULT_EXCHANGES: tuple[tuple[str, str, str], ...] = (
    ("binance", "BINANCE", "Binance"),
    ("bybit", "BYBIT", "Bybit"),
    ("coinbase", "COINBASE", "Coinbase"),
    ("kraken", "KRAKEN", "Kraken"),
    ("bitstamp", "BITSTAMP", "Bitstamp"),
    ("okx", "OKX", "OKX"),
    ("bitfinex", "BITFINEX", "Bitfinex"),
    ("kucoin", "KUCOIN", "KuCoin"),
    ("mexc", "MEXC", "MEXC"),
    ("gateio", "GATEIO", "Gate.io"),
    ("bitget", "BITGET", "Bitget"),
    ("cryptocom", "CRYPTOCOM", "Crypto.com"),
    ("gemini", "GEMINI", "Gemini"),
)


def build_default_providers() -> list[ExchangeProvider]:
    providers: list[ExchangeProvider] = []
    for exchange_id, tradingview_exchange, name in DEFAULT_EXCHANGES:
        try:
            providers.append(CcxtExchangeProvider(exchange_id, tradingview_exchange, name))
        except Exception:
            continue
    return providers
