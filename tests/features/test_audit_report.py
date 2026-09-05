"""Аудит: запись событий, окно 30 дней, группировка по юзерам, рендер."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from db import repos
from db.models import AuditEvent, Base
from db.session import make_sessionmaker, transaction
from features.audit.router import _report_text
from features.audit.service import AuditService


@pytest.fixture
async def sessionmaker():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def test_report_counts_by_action_and_skips_old(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await repos.add_user(
            session, tg_user_id=1, tg_username="alice", is_admin=True, invited_by_tg_id=None
        )
        await repos.add_user(
            session, tg_user_id=2, tg_username=None, is_admin=False, invited_by_tg_id=1
        )
        for _ in range(2):
            await repos.add_audit_event(
                session, actor_tg_id=1, action="mr.merge", target="proj/repo!42"
            )
        await repos.add_audit_event(
            session, actor_tg_id=1, action="tag.create", target="proj/repo@v1.0"
        )
        await repos.add_audit_event(
            session, actor_tg_id=2, action="job.play", target="proj/repo #7"
        )
        # старше окна — не должно попасть в счётчики
        session.add(
            AuditEvent(
                actor_tg_id=2,
                action="mr.merge",
                target="ancient",
                occurred_at=datetime.now() - timedelta(days=40),
            )
        )

    report = await AuditService(sessionmaker=sessionmaker).report(days=30, recent_limit=10)

    assert report.total == 4
    assert [a.tg_user_id for a in report.actors] == [1, 2]
    assert report.actors[0].by_action == [("mr.merge", 2), ("tag.create", 1)]
    assert report.actors[1].by_action == [("job.play", 1)]
    assert len(report.recent) == 5  # хвост не ограничен окном

    text = _report_text(report)
    assert "@alice</b> — 3" in text
    assert "mr.merge — 2" in text
    assert "<b>2</b> — 1" in text  # без username — по tg_user_id


async def test_empty_report(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    report = await AuditService(sessionmaker=sessionmaker).report()
    assert _report_text(report) == "За последние 30 дней действий не было."


async def test_report_filtered_by_actor(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await repos.add_user(
            session, tg_user_id=1, tg_username="Alice", is_admin=True, invited_by_tg_id=None
        )
        await repos.add_user(
            session, tg_user_id=2, tg_username="bob", is_admin=False, invited_by_tg_id=1
        )
        await repos.add_audit_event(
            session, actor_tg_id=1, action="mr.merge", target="proj/repo!42"
        )
        await repos.add_audit_event(
            session, actor_tg_id=2, action="job.play", target="proj/repo #7"
        )

    svc = AuditService(sessionmaker=sessionmaker)

    # username ищется без учёта регистра и лидирующей @
    target = await svc.find_user("@alice")
    assert target is not None and target.tg_user_id == 1
    assert (await svc.find_user("2")) is not None
    assert (await svc.find_user("nobody")) is None

    report = await svc.report(actor_ids=[target.tg_user_id])
    assert [a.tg_user_id for a in report.actors] == [1]
    assert [e.action for e in report.recent] == ["mr.merge"]


async def test_report_scoped_to_active_project(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """Видно только события активного проекта и вложенных в него."""
    async with transaction(sessionmaker) as session:
        members = ((1, "acme/platform"), (2, "acme/platform/backend"), (3, "other/project"))
        for tg_id, path in members:
            await repos.add_user(
                session,
                tg_user_id=tg_id,
                tg_username=f"u{tg_id}",
                is_admin=tg_id == 1,
                invited_by_tg_id=None,
            )
            conn = await repos.add_connection(
                session,
                owner_tg_id=tg_id,
                base_url="https://gl.example.com",
                scope_path=path,
                scope_kind="group",
                display_name=path,
                encrypted_pat=b"x",
                gitlab_user_id=tg_id,
                gitlab_username=f"gl{tg_id}",
            )
            await repos.set_default_connection(
                session, tg_user_id=tg_id, connection_id=conn.id
            )
            await repos.add_audit_event(
                session,
                actor_tg_id=tg_id,
                action="pipeline.run",
                target=path,
                base_url="https://gl.example.com",
                project_path=path,
            )
        me = await repos.get_user(session, 1)

    svc = AuditService(sessionmaker=sessionmaker)
    assert me is not None
    scope = await svc.scope_of(me)
    assert scope is not None
    assert scope.path == "acme/platform"

    report = await svc.report(scope=scope)
    # события 1 (ровно scope) и 2 (вложенный проект); 3 — из другого — нет
    assert sorted(a.tg_user_id for a in report.actors) == [1, 2]
    assert [e.target for e in report.recent] == ["acme/platform/backend", "acme/platform"]
    assert "<code>acme/platform</code>" in _report_text(report)
