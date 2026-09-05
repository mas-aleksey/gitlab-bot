"""Bot entrypoint: long-polling."""

import asyncio
import sys

from aiogram.types import BotCommand
from loguru import logger

from config import Settings
from deps import build, shutdown
from features.help.texts import MENU_COMMANDS


async def _run() -> None:
    settings = Settings()  # type: ignore[call-arg]
    logger.remove()
    logger.add(sys.stderr, level=settings.log_level)
    logger.info("starting bot (log level: {})", settings.log_level)

    deps = build(settings)
    try:
        await deps.bot.set_my_commands(
            [BotCommand(command=c, description=d) for c, d in MENU_COMMANDS]
        )
        await deps.dp.start_polling(deps.bot)
    finally:
        await shutdown(deps)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
