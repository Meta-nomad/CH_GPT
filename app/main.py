import asyncio
import logging

from app.bot.runner import run_bot
from app.core.config import Settings


async def main() -> None:
    settings = Settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    await run_bot(settings)


if __name__ == "__main__":
    asyncio.run(main())
