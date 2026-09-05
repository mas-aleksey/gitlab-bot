"""Middlewares for user + connection resolution.

Two layers:
- ``UserMiddleware`` (outer, on dispatcher): resolves the current ``User`` by
  ``tg_user_id`` and injects it as ``user``. ``user`` is ``None`` for guests.
- ``ConnectionMiddleware`` (inner, per-router): requires a registered user
  with a default connection, resolves it to ``GitLabAuth``. Handlers using
  the GitLab API only receive events past this gate.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.gitlab import GitLabAuth
from crypto import PatCrypto
from db.models import Connection, User
from db.repos import get_connection, get_user
from shared import texts

Handler = Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]]


class UserMiddleware(BaseMiddleware):
    """Resolve ``User`` by Telegram user id. Injects ``user`` (may be ``None``)."""

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if tg_user is None:
            data["user"] = None
            return await handler(event, data)

        sm: async_sessionmaker[AsyncSession] = data["sessionmaker"]
        async with sm() as session:
            data["user"] = await get_user(session, tg_user.id)
        return await handler(event, data)


class ConnectionMiddleware(BaseMiddleware):
    """Gate handlers behind a resolved default connection.

    Requires ``user`` (from ``UserMiddleware``) and ``crypto`` in ``data``.
    Injects:
    - ``connection``: the ``Connection`` ORM row (default connection of user)
    - ``auth``: ``GitLabAuth`` with decrypted PAT

    If the user is unknown or has no default connection, replies with a hint
    and stops propagation (via ``callback.answer`` for callbacks, plain
    ``message.answer`` for text commands).
    """

    async def __call__(
        self,
        handler: Handler,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("user")
        if user is None:
            await _reply(event, texts.NO_POOL)
            return None
        if user.default_connection_id is None:
            await _reply(event, texts.NO_CONNECTION)
            return None

        sm: async_sessionmaker[AsyncSession] = data["sessionmaker"]
        crypto: PatCrypto = data["crypto"]
        async with sm() as session:
            conn = await get_connection(session, user.default_connection_id)
        if conn is None:
            await _reply(event, texts.NO_CONNECTION)
            return None

        data["connection"] = conn
        data["auth"] = _to_auth(conn, crypto)
        return await handler(event, data)


def _to_auth(conn: Connection, crypto: PatCrypto) -> GitLabAuth:
    return GitLabAuth(base_url=conn.base_url, pat=crypto.decrypt(conn.encrypted_pat))


async def _reply(event: TelegramObject, text: str) -> None:
    if isinstance(event, Message):
        await event.answer(text)
    elif isinstance(event, CallbackQuery):
        await event.answer(text, show_alert=True)
