"""``/start``, ``/invite``, ``/pool`` + welcome-меню.

Registration only puts the caller into ``users`` — the first connection
is set up separately via ``/add_connection`` (connections feature).

Welcome-экран собирается здесь (`render_welcome`) и используется как из
`/start`, так и из callback'а ``NAV_WELCOME_CB`` (возврат в главное меню
с любого экрана).
"""

from datetime import UTC, date, datetime

from aiogram import Bot, F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import User
from db.repos import (
    get_connection,
    list_connections,
    set_default_connection,
)
from db.session import transaction
from features.onboarding import texts as t
from features.onboarding.service import (
    AlreadyRegistered,
    InviteAlreadyUsed,
    InviteInvalid,
    NotInPool,
    OnboardingService,
)
from shared import texts as shared_texts
from shared.edit import edit_or_answer
from shared.keyboards import add_main_menu, settings_keyboard, welcome_keyboard

router = Router(name="onboarding")


class SwitchCB(CallbackData, prefix="switch"):
    """Тап по строке экрана «Сменить проект» — id подключения."""

    id: int


async def render_welcome(
    user: User | None,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, InlineKeyboardMarkup]:
    """Собрать текст + клавиатуру главного меню.

    Для guest — только приветствие без кнопок.
    Для user — приветствие + строка про активное подключение.
    """
    if user is None:
        return t.WELCOME_GUEST, welcome_keyboard(None)

    async with sessionmaker() as session:
        conns = await list_connections(session, owner_tg_id=user.tg_user_id)

    conn_line = t.WELCOME_CONN_NONE
    active = next(
        (c for c in conns if c.id == user.default_connection_id), None
    )
    if active is not None:
        conn_line = t.WELCOME_CONN_ACTIVE.format(
            display_name=active.display_name,
            scope_path=active.scope_path,
        )
    text = t.WELCOME_MENU.format(name=_display(user), conn_line=conn_line)
    return text, welcome_keyboard(user, can_switch=len(conns) > 1)


@router.message(CommandStart(deep_link=False))
@router.message(CommandStart(deep_link=True))
async def on_start(
    message: Message,
    command: CommandObject,
    user: User | None,
    onboarding: OnboardingService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None:
        return

    if user is not None:
        text, markup = await render_welcome(user, sessionmaker)
        await message.answer(text, reply_markup=markup)
        return

    raw = (command.args or "").strip()
    invite_code = raw or None

    try:
        result = await onboarding.register(
            tg_user_id=message.from_user.id,
            tg_username=message.from_user.username,
            invite_code=invite_code,
            today=_today(),
        )
    except AlreadyRegistered:
        await message.answer(t.ALREADY_REGISTERED)
        return
    except NotInPool:
        await message.answer(t.WELCOME_GUEST)
        return
    except InviteInvalid:
        await message.answer(t.INVITE_INVALID)
        return
    except InviteAlreadyUsed:
        await message.answer(t.INVITE_USED)
        return

    intro = t.SEED_ADMIN_REGISTERED if result.was_seed_admin else t.INVITE_ACCEPTED
    text, markup = await render_welcome(result.user, sessionmaker)
    await message.answer(f"{intro}\n\n{text}", reply_markup=markup)


@router.callback_query(F.data == shared_texts.NAV_WELCOME_CB)
async def on_welcome_cb(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Возврат в главное меню из любого экрана. Сбрасывает FSM."""
    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = await render_welcome(user, sessionmaker)
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(F.data == shared_texts.NAV_SETTINGS_CB)
async def on_nav_settings(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
) -> None:
    """Открыть меню настроек."""
    await state.clear()
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await edit_or_answer(
        callback, shared_texts.SETTINGS_HEADER, settings_keyboard(user)
    )
    await callback.answer()


@router.callback_query(F.data == shared_texts.NAV_SWITCH_CB)
async def on_nav_switch(
    callback: CallbackQuery,
    user: User | None,
    state: FSMContext,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Экран смены активного проекта: строка = подключение, тап = переключились."""
    await state.clear()
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = await _render_switch(user, sessionmaker)
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(SwitchCB.filter())
async def on_switch(
    callback: CallbackQuery,
    callback_data: SwitchCB,
    user: User | None,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    """Переключить default и сразу вернуть главное меню — тап и работаешь дальше."""
    if user is None or not isinstance(callback.message, Message):
        await callback.answer()
        return

    async with sessionmaker() as session:
        conn = await get_connection(session, callback_data.id)
    if conn is None or conn.owner_tg_id != user.tg_user_id:
        await callback.answer(t.SWITCH_EMPTY, show_alert=True)
        return

    async with transaction(sessionmaker) as session:
        await set_default_connection(
            session, tg_user_id=user.tg_user_id, connection_id=conn.id
        )
    # ``user`` пришёл из middleware до записи — обновляем, иначе меню
    # отрисуется со старым активным подключением.
    user.default_connection_id = conn.id

    text, markup = await render_welcome(user, sessionmaker)
    await edit_or_answer(callback, text, markup)
    await callback.answer(t.SWITCH_OK.format(display_name=conn.display_name))


async def _render_switch(
    user: User,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> tuple[str, InlineKeyboardMarkup]:
    async with sessionmaker() as session:
        conns = await list_connections(session, owner_tg_id=user.tg_user_id)
    if not conns:
        kb = InlineKeyboardBuilder()
        add_main_menu(kb)
        return t.SWITCH_EMPTY, kb.as_markup()

    kb = InlineKeyboardBuilder()
    for c in conns:
        mark = (
            t.SWITCH_MARK_ACTIVE
            if c.id == user.default_connection_id
            else t.SWITCH_MARK_IDLE
        )
        kb.button(
            text=t.SWITCH_ROW.format(
                mark=mark, display_name=c.display_name, scope_path=c.scope_path
            ),
            callback_data=SwitchCB(id=c.id).pack(),
        )
    kb.adjust(1)
    add_main_menu(kb)
    return t.SWITCH_ASK, kb.as_markup()


@router.callback_query(F.data == shared_texts.NAV_INVITE_CB)
async def on_nav_invite(
    callback: CallbackQuery,
    bot: Bot,
    user: User | None,
    onboarding: OnboardingService,
) -> None:
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    try:
        code = await onboarding.issue_invite(
            issuer_tg_id=user.tg_user_id, today=_today()
        )
    except NotInPool:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    me = await bot.me()
    deeplink = f"https://t.me/{me.username}?start={code}" if me.username else code
    await callback.message.answer(t.INVITE_MESSAGE.format(code=code, deeplink=deeplink))
    await callback.answer()


@router.callback_query(F.data == shared_texts.NAV_POOL_CB)
async def on_nav_pool(
    callback: CallbackQuery,
    user: User | None,
    onboarding: OnboardingService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await callback.message.answer(
        await _pool_response(user, onboarding, sessionmaker, show_all=False)
    )
    await callback.answer()


@router.message(Command("invite"))
async def on_invite(
    message: Message,
    bot: Bot,
    onboarding: OnboardingService,
) -> None:
    if message.from_user is None:
        return
    try:
        code = await onboarding.issue_invite(
            issuer_tg_id=message.from_user.id, today=_today()
        )
    except NotInPool:
        await message.answer(shared_texts.NO_POOL)
        return

    me = await bot.me()
    deeplink = f"https://t.me/{me.username}?start={code}" if me.username else code
    await message.answer(t.INVITE_MESSAGE.format(code=code, deeplink=deeplink))


@router.message(Command("pool"))
async def on_pool(
    message: Message,
    command: CommandObject,
    user: User | None,
    onboarding: OnboardingService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if user is None:
        await message.answer(shared_texts.NO_POOL)
        return
    show_all = (command.args or "").strip().lower() == "all"
    await message.answer(
        await _pool_response(user, onboarding, sessionmaker, show_all=show_all)
    )


async def _pool_response(
    user: User,
    onboarding: OnboardingService,
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    show_all: bool,
) -> str:
    """``/pool`` — участники текущего scope, ``/pool all`` — весь pool.

    Scope берётся из активного подключения: видно тех, кто подключён к тому же
    проекту/группе (или вложенным). Без подключения показывать нечего — scope
    неизвестен.
    """
    if show_all:
        members = await onboarding.list_pool()
        return (t.POOL_EMPTY if not members else _pool_text(members) + t.POOL_ALL_HINT)

    if user.default_connection_id is None:
        return t.POOL_NO_CONNECTION
    async with sessionmaker() as session:
        conn = await get_connection(session, user.default_connection_id)
    if conn is None:
        return t.POOL_NO_CONNECTION

    in_scope = await onboarding.list_pool_in_scope(
        base_url=conn.base_url, scope_path=conn.scope_path
    )
    if not [m for m, _ in in_scope if m.tg_user_id != user.tg_user_id]:
        return t.POOL_SCOPE_EMPTY.format(scope_path=conn.scope_path)
    return _pool_scope_text(in_scope, scope_path=conn.scope_path)


def _pool_text(members: list[User]) -> str:
    lines = [t.POOL_HEADER.format(count=len(members))]
    lines.extend(_row(m) for m in members)
    return "\n".join(lines)


def _pool_scope_text(members: list[tuple[User, str]], *, scope_path: str) -> str:
    lines = [t.POOL_SCOPE_HEADER.format(scope_path=scope_path, count=len(members))]
    lines.extend(
        _row(m) + (
            t.POOL_ROW_SCOPE.format(scope_path=member_scope)
            if member_scope != scope_path
            else ""
        )
        for m, member_scope in members
    )
    return "\n".join(lines)


def _row(m: User) -> str:
    return t.POOL_ROW.format(
        name=_display(m),
        tg_user_id=m.tg_user_id,
        admin_tag=t.POOL_ADMIN_TAG if m.is_admin else "",
    )


def _display(user: User) -> str:
    return user.tg_username or str(user.tg_user_id)


def _today() -> date:
    return datetime.now(tz=UTC).date()
