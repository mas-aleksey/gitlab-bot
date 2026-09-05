"""Edit-or-answer helper for callback screens.

Unifies the three near-identical ``_edit`` helpers previously duplicated in
per-feature handler modules. Callers pass the ``CallbackQuery`` and get
back either an in-place edit or a fresh message on failure. Silently ignores
"message is not modified" — that is expected when a refresh yields the same
markup.
"""

from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from loguru import logger


async def edit_or_answer(
    callback: CallbackQuery,
    text: str,
    markup: InlineKeyboardMarkup | None,
    *,
    disable_web_page_preview: bool = True,
) -> None:
    msg = callback.message
    if not isinstance(msg, Message):
        return
    try:
        await msg.edit_text(
            text,
            reply_markup=markup,
            disable_web_page_preview=disable_web_page_preview,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e):
            return
        logger.warning("could not edit message: {}", e)
        await msg.answer(
            text,
            reply_markup=markup,
            disable_web_page_preview=disable_web_page_preview,
        )
