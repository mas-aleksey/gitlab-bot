"""``/audit`` — сводка действий пользователей за 30 дней. Только для админа.

Счётчики по типам действий на каждого юзера + хвост последних 10 событий.
Без аргумента — события активного проекта/группы (scope подключения);
``/audit @username`` — то же по одному человеку; ``/audit all`` — вообще всё,
включая другие проекты и действия вне проектов (инвайты, регистрации).
Пишут события хендлеры через ``shared.audit.log_action``.
"""

from html import escape

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from db.models import User
from features.audit import texts as t
from features.audit.service import AuditReport, AuditService
from shared import texts as shared_texts

router = Router(name="audit")

_DAYS = 30
_RECENT = 10


@router.message(Command("audit"))
async def on_audit(
    message: Message,
    command: CommandObject,
    user: User | None,
    audit: AuditService,
) -> None:
    if user is None:
        await message.answer(shared_texts.NO_POOL)
        return
    if not user.is_admin:
        await message.answer(t.AUDIT_NOT_ADMIN)
        return

    ref = (command.args or "").strip()

    if ref.lower() == "all":
        report = await audit.report(days=_DAYS, recent_limit=_RECENT)
        await message.answer(_report_text(report) + t.AUDIT_ALL_HINT)
        return

    if not ref:
        scope = await audit.scope_of(user)
        if scope is None:
            await message.answer(t.AUDIT_NO_CONNECTION)
            return
        report = await audit.report(days=_DAYS, recent_limit=_RECENT, scope=scope)
        if not report.actors:
            await message.answer(
                t.AUDIT_EMPTY_SCOPE.format(scope_path=scope.path, days=_DAYS)
            )
            return
        await message.answer(_report_text(report) + t.AUDIT_HINT)
        return

    target = await audit.find_user(ref)
    if target is None:
        await message.answer(t.AUDIT_NO_SUCH_USER.format(ref=escape(ref)))
        return
    # по человеку — в том же срезе, что и общий отчёт: активный проект
    report = await audit.report(
        days=_DAYS,
        recent_limit=_RECENT,
        actor_ids=[target.tg_user_id],
        scope=await audit.scope_of(user),
    )
    name = _name(target.tg_username, target.tg_user_id)
    if not report.actors:
        await message.answer(t.AUDIT_EMPTY_ACTOR.format(name=name, days=_DAYS))
        return
    await message.answer(_report_text(report))


def _report_text(report: AuditReport) -> str:
    if not report.actors:
        return t.AUDIT_EMPTY.format(days=report.days)

    header = (
        t.AUDIT_HEADER_SCOPE.format(
            days=report.days, scope_path=report.scope_path, total=report.total
        )
        if report.scope_path is not None
        else t.AUDIT_HEADER.format(days=report.days, total=report.total)
    )
    parts = [header]
    for actor in report.actors:
        parts.append(
            t.AUDIT_ACTOR.format(
                name=_name(actor.username, actor.tg_user_id), total=actor.total
            )
        )
        parts.extend(
            t.AUDIT_ACTOR_ACTION.format(action=action, n=n)
            for action, n in actor.by_action
        )

    if report.recent:
        parts.append(t.AUDIT_RECENT_HEADER)
        parts.extend(
            t.AUDIT_RECENT_ROW.format(
                when=e.occurred_at.strftime("%d.%m %H:%M"),
                name=_name(e.username, e.tg_user_id),
                action=e.action,
                target=escape(e.target),
            ).rstrip()
            for e in report.recent
        )
    return "".join(parts)


def _name(username: str | None, tg_user_id: int) -> str:
    return f"@{escape(username)}" if username else str(tg_user_id)
