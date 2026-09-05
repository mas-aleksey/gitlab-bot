"""Запись аудит-событий из хендлеров.

Хендлер получает ``sessionmaker`` из workflow_data и зовёт ``log_action``
после успешной мутации. Ошибка записи не должна ронять само действие —
логируем и идём дальше.
"""

from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db import repos
from db.session import transaction


async def log_action(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    actor_tg_id: int,
    action: str,
    target: str = "",
    base_url: str | None = None,
    project_path: str | None = None,
) -> None:
    """``base_url`` + ``project_path`` задают scope события — по ним ``/audit``
    режет отчёт под активное подключение. Без них событие видно только в
    ``/audit all`` (действия вне проекта: инвайты, регистрация)."""
    try:
        async with transaction(sessionmaker) as session:
            await repos.add_audit_event(
                session,
                actor_tg_id=actor_tg_id,
                action=action,
                target=target,
                base_url=base_url,
                project_path=project_path,
            )
    except Exception as e:
        logger.warning("audit write failed: action={} err={}", action, e)
