"""Global aiogram error handler.

Catches unhandled exceptions inside handlers and turns them into a
short user-facing message. GitLab-specific errors get dedicated texts;
everything else falls back to ``ERR_UNEXPECTED``.

Middleware/handler errors bubble up here through ``dp.errors``.
"""

from aiogram import Dispatcher
from aiogram.types import ErrorEvent, Message
from loguru import logger

from clients.gitlab import (
    GitLabAuthError,
    GitLabBadRequest,
    GitLabConflict,
    GitLabError,
    GitLabForbidden,
    GitLabNotFound,
)
from shared import texts


def register(dp: Dispatcher) -> None:
    dp.errors.register(_on_error)


async def _on_error(event: ErrorEvent) -> bool:
    exc = event.exception
    text = _map(exc)

    message = _extract_message(event)
    if message is not None:
        try:
            await message.answer(text)
        except Exception:
            logger.exception("failed to send error reply")

    if isinstance(exc, GitLabError):
        logger.warning("gitlab error: {}", exc)
    else:
        logger.exception("unhandled error: {}", exc)
    return True


def _map(exc: BaseException) -> str:
    if isinstance(exc, GitLabAuthError):
        return texts.ERR_AUTH
    if isinstance(exc, GitLabForbidden):
        return texts.ERR_FORBIDDEN
    if isinstance(exc, GitLabNotFound):
        return texts.ERR_NOT_FOUND
    if isinstance(exc, GitLabConflict):
        return texts.ERR_CONFLICT
    if isinstance(exc, GitLabBadRequest):
        return texts.ERR_BAD_REQUEST
    if isinstance(exc, GitLabError):
        return texts.ERR_GITLAB_GENERIC.format(message=str(exc))
    return texts.ERR_UNEXPECTED


def _extract_message(event: ErrorEvent) -> Message | None:
    update = event.update
    if update.message is not None:
        return update.message
    if update.callback_query is not None and isinstance(
        update.callback_query.message, Message
    ):
        return update.callback_query.message
    return None
