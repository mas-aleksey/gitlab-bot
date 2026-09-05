"""Shared keyboard fragments and builders used across features."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import InlineKeyboardBuilder

from db.models import User
from shared import texts


def add_main_menu(kb: InlineKeyboardBuilder) -> None:
    """Прикрепить кнопку «← Главное меню» отдельным рядом в конец клавиатуры."""
    kb.row(
        InlineKeyboardButton(
            text=texts.BTN_MAIN_MENU,
            callback_data=texts.NAV_WELCOME_CB,
        )
    )


def add_settings_menu(kb: InlineKeyboardBuilder) -> None:
    """Прикрепить кнопку «← Настройки» отдельным рядом в конец клавиатуры."""
    kb.row(
        InlineKeyboardButton(
            text=texts.BTN_SETTINGS_MENU,
            callback_data=texts.NAV_SETTINGS_CB,
        )
    )


def welcome_keyboard(
    user: User | None, *, can_switch: bool = False
) -> InlineKeyboardMarkup:
    """Главное меню. Guest'ы (user=None) не получают кнопок.

    ``can_switch`` — у юзера больше одного подключения, показываем «Сменить
    проект» прямо здесь (иначе смена default — 4 тапа через настройки).
    """
    kb = InlineKeyboardBuilder()
    if user is None:
        return kb.as_markup()
    kb.button(text=texts.BTN_MRS, callback_data=texts.NAV_MRS_CB)
    kb.button(text=texts.BTN_PIPELINES, callback_data=texts.NAV_PIPELINES_CB)
    kb.button(text=texts.BTN_TAGS, callback_data=texts.NAV_TAGS_CB)
    kb.button(text=texts.BTN_RELEASES, callback_data=texts.NAV_RELEASES_CB)
    if can_switch:
        kb.button(text=texts.BTN_SWITCH, callback_data=texts.NAV_SWITCH_CB)
    kb.button(text=texts.BTN_SETTINGS, callback_data=texts.NAV_SETTINGS_CB)
    kb.adjust(1)
    return kb.as_markup()


def settings_keyboard(user: User | None) -> InlineKeyboardMarkup:
    """Меню настроек. Guest'ы (user=None) не получают кнопок."""
    kb = InlineKeyboardBuilder()
    if user is None:
        return kb.as_markup()
    kb.button(text=texts.BTN_CONNECTIONS, callback_data=texts.NAV_CONNECTIONS_CB)
    kb.button(text=texts.BTN_INVITE, callback_data=texts.NAV_INVITE_CB)
    kb.button(text=texts.BTN_POOL, callback_data=texts.NAV_POOL_CB)
    kb.button(text=texts.BTN_HELP, callback_data=texts.NAV_HELP_CB)
    kb.adjust(1)
    add_main_menu(kb)
    return kb.as_markup()
