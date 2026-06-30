from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher, F, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.bot.formatting import format_analysis, format_compare
from app.core.analyzer import ChartAnalyzer
from app.core.config import Settings
from app.providers.registry import build_default_providers, build_mexc_futures_checker
from app.providers.tradingview import TradingViewClient
from app.storage.cache import AnalysisCache

logger = logging.getLogger(__name__)


def build_router(analyzer: ChartAnalyzer) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        await message.answer(
            "Пришли тикер криптовалюты, например BTC, ETH, SOL или SUI.\n\n"
            "Я найду лучший график для TradingView: самая длинная история, правильная пара "
            "USD/USDT и минимум дефектов на часовых свечах."
        )

    @router.message(Command("why"))
    async def why(message: Message) -> None:
        query = _command_arg(message)
        if not query:
            await message.answer("Напиши так: /why BTC")
            return
        await _answer_analysis(message, analyzer, query)

    @router.message(Command("tvtest"))
    async def tvtest(message: Message) -> None:
        query = _command_arg(message)
        if not query:
            await message.answer("Напиши так: /tvtest BTC")
            return
        status = await message.answer("Проверяю доступ TradingView...")
        try:
            result = await asyncio.wait_for(analyzer.probe_tradingview(query), timeout=25)
        except TimeoutError:
            result = "TradingView не ответил за 25 секунд. Это уже не зависание бота, а недоступность источника/биржевого поиска."
        await status.edit_text(result)

    @router.message(Command("compare"))
    async def compare(message: Message) -> None:
        query = _command_arg(message)
        if not query:
            await message.answer("Напиши так: /compare BTC")
            return
        status = await message.answer("Сравниваю графики по биржам...")
        result = await analyzer.analyze(query)
        await status.edit_text(format_compare(result))

    @router.message(F.text)
    async def analyze_text(message: Message) -> None:
        await _answer_analysis(message, analyzer, message.text or "")

    return router


async def run_bot(settings: Settings) -> None:
    if not settings.bot_token:
        raise RuntimeError("BOT_TOKEN is required")

    cache = AnalysisCache(settings.cache_db_path, settings.cache_ttl_seconds)
    providers = build_default_providers()
    mexc_futures_checker = build_mexc_futures_checker()
    tradingview_client = TradingViewClient()
    analyzer = ChartAnalyzer(
        providers,
        cache,
        max_candles=settings.max_candles,
        quote_policy_year=settings.quote_policy_year,
        mexc_futures_checker=mexc_futures_checker,
        tradingview_client=tradingview_client,
    )

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(analyzer))

    try:
        logger.info("Bot started with %d exchange providers", len(providers))
        await dispatcher.start_polling(bot)
    finally:
        for provider in providers:
            await provider.close()
        await mexc_futures_checker.close()
        await tradingview_client.close()
        await bot.session.close()


async def _answer_analysis(message: Message, analyzer: ChartAnalyzer, query: str) -> None:
    cleaned = query.strip()
    if not cleaned:
        await message.answer("Пришли тикер, например BTC.")
        return

    status = await message.answer("Анализирую графики по биржам...")
    result = await analyzer.analyze(cleaned)
    await status.edit_text(format_analysis(result))


def _command_arg(message: Message) -> str:
    text = message.text or ""
    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""
