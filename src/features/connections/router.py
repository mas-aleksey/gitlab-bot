"""``/add_connection`` + ``/connections`` — управление GitLab-подключениями.

``/add_connection`` — FSM в 3 шага (scope URL → display_name → PAT).

``/connections`` — inline-меню: список → карточка (сделать default / удалить /
rotate PAT / назад). Rotate PAT — отдельная FSM с одним шагом ввода нового
токена.

Все сообщения с PAT удаляются best-effort сразу после чтения, до вызова
GitLab: даже при провале валидации токен не остаётся в чате.
"""

from typing import Literal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db.models import Connection, User
from features.connections import texts as t
from features.connections.scope_url import ScopeUrlParseError, parse_scope_url
from features.connections.service import (
    ConnectionNotFound,
    ConnectionsService,
    ConnectionsView,
    DuplicateConnection,
    PatOwnerMismatch,
)
from features.connections.states import AddConnectionState, RotatePatState
from shared import texts as shared_texts
from shared.audit import log_action
from shared.edit import edit_or_answer
from shared.keyboards import add_settings_menu

router = Router(name="connections")


_BASE_URL_KEY = "base_url"
_SCOPE_PATH_KEY = "scope_path"
_DISPLAY_NAME_KEY = "display_name"
_ROTATE_CONN_ID_KEY = "rotate_connection_id"


class ConnCB(CallbackData, prefix="conn"):
    action: Literal["view", "default", "del_ask", "del_yes", "rotate", "back"]
    id: int


@router.message(Command("cancel"))
async def on_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        return
    await state.clear()
    await message.answer(t.CONN_CANCELLED)


@router.callback_query(F.data == shared_texts.NAV_ADD_CONNECTION_CB)
async def on_nav_add_connection(
    callback: CallbackQuery,
    state: FSMContext,
    user: User | None,
) -> None:
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    await state.clear()
    await state.set_state(AddConnectionState.waiting_for_url)
    await callback.message.answer(t.CONN_ASK_URL)
    await callback.answer()


@router.callback_query(F.data == shared_texts.NAV_CONNECTIONS_CB)
async def on_nav_connections(
    callback: CallbackQuery,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None:
        await callback.answer(shared_texts.NO_POOL, show_alert=True)
        return
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    view = await connections.list_for_user(owner_tg_id=user.tg_user_id)
    if not view.items:
        kb = InlineKeyboardBuilder()
        kb.button(
            text=shared_texts.BTN_ADD_CONNECTION,
            callback_data=shared_texts.NAV_ADD_CONNECTION_CB,
        )
        kb.adjust(1)
        add_settings_menu(kb)
        await edit_or_answer(callback, t.CONN_LIST_EMPTY, kb.as_markup())
        await callback.answer()
        return
    await edit_or_answer(callback, _list_text(view), _list_keyboard(view))
    await callback.answer()


@router.message(Command("add_connection"))
async def on_add_connection(
    message: Message,
    state: FSMContext,
    user: User | None,
) -> None:
    if user is None:
        await message.answer(shared_texts.NO_POOL)
        return
    await state.clear()
    await state.set_state(AddConnectionState.waiting_for_url)
    await message.answer(t.CONN_ASK_URL)


@router.message(AddConnectionState.waiting_for_url, F.text)
async def on_url(message: Message, state: FSMContext) -> None:
    assert message.text is not None
    try:
        base_url, scope_path = parse_scope_url(message.text)
    except ScopeUrlParseError as e:
        await message.answer(t.CONN_URL_BAD.format(reason=e.args[0]))
        return
    await state.update_data({_BASE_URL_KEY: base_url, _SCOPE_PATH_KEY: scope_path})
    await state.set_state(AddConnectionState.waiting_for_display_name)
    await message.answer(t.CONN_ASK_DISPLAY_NAME)


@router.message(AddConnectionState.waiting_for_display_name, F.text)
async def on_display_name(message: Message, state: FSMContext) -> None:
    assert message.text is not None
    display_name = message.text.strip()
    if not display_name:
        return
    await state.update_data({_DISPLAY_NAME_KEY: display_name})
    await state.set_state(AddConnectionState.waiting_for_pat)
    data = await state.get_data()
    await message.answer(t.CONN_ASK_PAT.format(base_url=data[_BASE_URL_KEY]))


@router.message(AddConnectionState.waiting_for_pat, F.text)
async def on_pat(
    message: Message,
    state: FSMContext,
    user: User | None,
    connections: ConnectionsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert message.text is not None
    if user is None:
        await state.clear()
        await message.answer(shared_texts.NO_POOL)
        return

    pat = message.text.strip()
    data = await state.get_data()
    base_url = data.get(_BASE_URL_KEY)
    scope_path = data.get(_SCOPE_PATH_KEY)
    display_name = data.get(_DISPLAY_NAME_KEY)
    if not (
        isinstance(base_url, str)
        and isinstance(scope_path, str)
        and isinstance(display_name, str)
    ):
        await state.clear()
        await message.answer(shared_texts.ERR_UNEXPECTED)
        return

    await _delete_pat_message(message)

    try:
        result = await connections.register(
            owner_tg_id=user.tg_user_id,
            base_url=base_url,
            scope_path=scope_path,
            display_name=display_name,
            pat=pat,
        )
    except DuplicateConnection:
        await state.clear()
        await message.answer(t.CONN_DUPLICATE)
        return

    await state.clear()
    await log_action(
        sessionmaker,
        actor_tg_id=user.tg_user_id,
        action="connection.add",
        target=f"{result.connection.display_name} ({result.connection.scope_path})",
        base_url=result.connection.base_url,
        project_path=result.connection.scope_path,
    )
    default_note = t.CONN_MADE_DEFAULT if result.made_default else ""
    await message.answer(
        t.CONN_REGISTERED.format(
            display_name=result.connection.display_name,
            scope_path=result.connection.scope_path,
            scope_kind=result.connection.scope_kind,
            gitlab_username=result.connection.gitlab_username,
            default_note=default_note,
        )
    )


async def _delete_pat_message(message: Message) -> None:
    try:
        await message.delete()
    except Exception as e:
        logger.warning("could not delete PAT message: {}", e)


# ---------- /connections ----------


@router.message(Command("connections"))
async def on_connections(
    message: Message,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None:
        await message.answer(shared_texts.NO_POOL)
        return
    view = await connections.list_for_user(owner_tg_id=user.tg_user_id)
    if not view.items:
        await message.answer(t.CONN_LIST_EMPTY)
        return
    await message.answer(_list_text(view), reply_markup=_list_keyboard(view))


@router.callback_query(ConnCB.filter(F.action == "back"))
async def on_back(
    callback: CallbackQuery,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    view = await connections.list_for_user(owner_tg_id=user.tg_user_id)
    if not view.items:
        await edit_or_answer(callback, t.CONN_LIST_EMPTY, None)
        await callback.answer()
        return
    await edit_or_answer(callback, _list_text(view), _list_keyboard(view))
    await callback.answer()


@router.callback_query(ConnCB.filter(F.action == "view"))
async def on_view(
    callback: CallbackQuery,
    callback_data: ConnCB,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    try:
        conn = await connections.get_owned(
            owner_tg_id=user.tg_user_id, connection_id=callback_data.id
        )
    except ConnectionNotFound:
        await callback.answer(t.CONN_NOT_FOUND, show_alert=True)
        return
    view = await connections.list_for_user(owner_tg_id=user.tg_user_id)
    is_default = view.default_id == conn.id
    await edit_or_answer(
        callback, _view_text(conn, is_default), _view_keyboard(conn, is_default)
    )
    await callback.answer()


@router.callback_query(ConnCB.filter(F.action == "default"))
async def on_set_default(
    callback: CallbackQuery,
    callback_data: ConnCB,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    try:
        conn = await connections.set_default(
            owner_tg_id=user.tg_user_id, connection_id=callback_data.id
        )
    except ConnectionNotFound:
        await callback.answer(t.CONN_NOT_FOUND, show_alert=True)
        return
    await edit_or_answer(callback, _view_text(conn, is_default=True), _view_keyboard(conn, True))
    await callback.answer(t.CONN_SET_DEFAULT_OK.format(display_name=conn.display_name))


@router.callback_query(ConnCB.filter(F.action == "del_ask"))
async def on_delete_ask(
    callback: CallbackQuery,
    callback_data: ConnCB,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    try:
        conn = await connections.get_owned(
            owner_tg_id=user.tg_user_id, connection_id=callback_data.id
        )
    except ConnectionNotFound:
        await callback.answer(t.CONN_NOT_FOUND, show_alert=True)
        return
    kb = InlineKeyboardBuilder()
    kb.button(
        text=t.CONN_BTN_CONFIRM_DELETE,
        callback_data=ConnCB(action="del_yes", id=conn.id).pack(),
    )
    kb.button(
        text=t.CONN_BTN_CANCEL,
        callback_data=ConnCB(action="view", id=conn.id).pack(),
    )
    kb.adjust(2)
    add_settings_menu(kb)
    await edit_or_answer(
        callback,
        t.CONN_DELETE_ASK.format(display_name=conn.display_name),
        kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(ConnCB.filter(F.action == "del_yes"))
async def on_delete_confirm(
    callback: CallbackQuery,
    callback_data: ConnCB,
    user: User | None,
    connections: ConnectionsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    try:
        conn = await connections.get_owned(
            owner_tg_id=user.tg_user_id, connection_id=callback_data.id
        )
    except ConnectionNotFound:
        await callback.answer(t.CONN_NOT_FOUND, show_alert=True)
        return
    display_name = conn.display_name
    result = await connections.delete(
        owner_tg_id=user.tg_user_id, connection_id=conn.id
    )
    await log_action(
        sessionmaker,
        actor_tg_id=user.tg_user_id,
        action="connection.delete",
        target=display_name,
        base_url=conn.base_url,
        project_path=conn.scope_path,
    )
    text = t.CONN_DELETED.format(display_name=display_name)
    if result.was_default:
        if result.new_default is not None:
            text += t.CONN_DELETED_NEW_DEFAULT.format(
                display_name=result.new_default.display_name
            )
        else:
            text += t.CONN_DELETED_NO_DEFAULT

    view = await connections.list_for_user(owner_tg_id=user.tg_user_id)
    if view.items:
        text = f"{text}\n\n{_list_text(view)}"
        await edit_or_answer(callback, text, _list_keyboard(view))
    else:
        await edit_or_answer(callback, text, None)
    await callback.answer()


@router.callback_query(ConnCB.filter(F.action == "rotate"))
async def on_rotate_start(
    callback: CallbackQuery,
    callback_data: ConnCB,
    state: FSMContext,
    user: User | None,
    connections: ConnectionsService,
) -> None:
    if user is None or callback.message is None:
        await callback.answer()
        return
    try:
        conn = await connections.get_owned(
            owner_tg_id=user.tg_user_id, connection_id=callback_data.id
        )
    except ConnectionNotFound:
        await callback.answer(t.CONN_NOT_FOUND, show_alert=True)
        return
    await state.clear()
    await state.set_state(RotatePatState.waiting_for_pat)
    await state.update_data({_ROTATE_CONN_ID_KEY: conn.id})
    await callback.answer()
    if isinstance(callback.message, Message):
        await callback.message.answer(
            t.CONN_ROTATE_ASK.format(
                display_name=conn.display_name,
                gitlab_username=conn.gitlab_username,
            )
        )


@router.message(RotatePatState.waiting_for_pat, F.text)
async def on_rotate_pat(
    message: Message,
    state: FSMContext,
    user: User | None,
    connections: ConnectionsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    assert message.text is not None
    if user is None:
        await state.clear()
        await message.answer(shared_texts.NO_POOL)
        return

    data = await state.get_data()
    connection_id = data.get(_ROTATE_CONN_ID_KEY)
    if not isinstance(connection_id, int):
        await state.clear()
        await message.answer(shared_texts.ERR_UNEXPECTED)
        return

    pat = message.text.strip()
    await _delete_pat_message(message)

    try:
        conn = await connections.rotate_pat(
            owner_tg_id=user.tg_user_id,
            connection_id=connection_id,
            new_pat=pat,
        )
    except ConnectionNotFound:
        await state.clear()
        await message.answer(t.CONN_NOT_FOUND)
        return
    except PatOwnerMismatch:
        await state.clear()
        await message.answer(t.CONN_ROTATE_OWNER_MISMATCH)
        return

    await state.clear()
    await log_action(
        sessionmaker,
        actor_tg_id=user.tg_user_id,
        action="connection.rotate_pat",
        target=conn.display_name,
        base_url=conn.base_url,
        project_path=conn.scope_path,
    )
    await message.answer(t.CONN_ROTATE_OK.format(display_name=conn.display_name))


# ---------- rendering helpers ----------


def _list_text(view: ConnectionsView) -> str:
    lines = [t.CONN_LIST_HEADER]
    for conn in view.items:
        default_mark = t.CONN_DEFAULT_MARK if conn.id == view.default_id else ""
        lines.append(
            t.CONN_LIST_ROW.format(
                default_mark=default_mark,
                display_name=conn.display_name,
                scope_path=conn.scope_path,
            )
        )
    return "\n".join(lines)


def _list_keyboard(view: ConnectionsView) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for conn in view.items:
        prefix = t.CONN_DEFAULT_MARK if conn.id == view.default_id else ""
        kb.button(
            text=f"{prefix}{conn.display_name}",
            callback_data=ConnCB(action="view", id=conn.id).pack(),
        )
    kb.button(
        text=shared_texts.BTN_ADD_CONNECTION,
        callback_data=shared_texts.NAV_ADD_CONNECTION_CB,
    )
    kb.adjust(1)
    add_settings_menu(kb)
    return kb.as_markup()


def _view_text(conn: Connection, is_default: bool) -> str:
    return t.CONN_VIEW.format(
        display_name=conn.display_name,
        default_tag=t.CONN_VIEW_DEFAULT_TAG if is_default else "",
        scope_path=conn.scope_path,
        scope_kind=conn.scope_kind,
        base_url=conn.base_url,
        gitlab_username=conn.gitlab_username,
    )


def _view_keyboard(conn: Connection, is_default: bool) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    if not is_default:
        kb.button(
            text=t.CONN_BTN_SET_DEFAULT,
            callback_data=ConnCB(action="default", id=conn.id).pack(),
        )
    kb.button(
        text=t.CONN_BTN_ROTATE_PAT,
        callback_data=ConnCB(action="rotate", id=conn.id).pack(),
    )
    kb.button(
        text=t.CONN_BTN_DELETE,
        callback_data=ConnCB(action="del_ask", id=conn.id).pack(),
    )
    kb.button(
        text=t.CONN_BTN_BACK,
        callback_data=ConnCB(action="back", id=0).pack(),
    )
    kb.adjust(1)
    add_settings_menu(kb)
    return kb.as_markup()
