"""``/help`` — статичная справка по командам.

Для guest (нет в pool) и registered user показываем разные секции.
Кнопка «← Главное меню» в конце — для guest её нет (в welcome-меню
у guest всё равно нет кнопок).
"""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.models import User
from features.help import texts as t
from shared import texts as shared_texts
from shared.edit import edit_or_answer
from shared.keyboards import add_settings_menu

router = Router(name="help")


def _render(user: User | None) -> tuple[str, InlineKeyboardMarkup | None]:
    body = t.HELP_USER if user is not None else t.HELP_GUEST
    if user is not None and user.is_admin:
        body += t.HELP_ADMIN
    text = t.HELP_HEADER + body
    if user is None:
        return text, None
    kb = InlineKeyboardBuilder()
    add_settings_menu(kb)
    return text, kb.as_markup()


@router.message(Command("help"))
async def on_help(message: Message, user: User | None) -> None:
    text, markup = _render(user)
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == shared_texts.NAV_HELP_CB)
async def on_nav_help(callback: CallbackQuery, user: User | None) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = _render(user)
    await edit_or_answer(callback, text, markup)
    await callback.answer()
