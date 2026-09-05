"""Pool по scope: кто виден из данного проекта/группы."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from db import repos
from db.models import Base
from db.session import make_sessionmaker, transaction

GL = "https://gl.example.com"
OTHER_GL = "https://gl.other.com"


@pytest.fixture
async def sessionmaker():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _add(session, tg_user_id: int, scope_path: str, base_url: str = GL) -> None:  # type: ignore[no-untyped-def]
    await repos.add_user(
        session,
        tg_user_id=tg_user_id,
        tg_username=f"u{tg_user_id}",
        is_admin=False,
        invited_by_tg_id=None,
    )
    await repos.add_connection(
        session,
        owner_tg_id=tg_user_id,
        base_url=base_url,
        scope_path=scope_path,
        scope_kind="group",
        display_name=scope_path,
        encrypted_pat=b"x",
        gitlab_user_id=tg_user_id,
        gitlab_username=f"gl{tg_user_id}",
    )


async def test_scope_matches_nested_but_not_sibling_prefix(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await _add(session, 1, "acme/platform")           # ровно тот же scope
        await _add(session, 2, "acme/platform/backend")      # вложенный проект
        await _add(session, 3, "acme")              # родительская группа
        await _add(session, 4, "acme/platformx")          # общий префикс, но чужой
        await _add(session, 5, "other/project")             # другой scope
        await _add(session, 6, "acme/platform", OTHER_GL)  # тот же путь, другой инстанс

    async with sessionmaker() as session:
        found = await repos.list_pool_in_scope(
            session, base_url=GL, scope_path="acme/platform"
        )

    assert [u.tg_user_id for u, _ in found] == [1, 2, 3]
    assert [scope for _, scope in found] == [
        "acme/platform",
        "acme/platform/backend",
        "acme",
    ]


async def test_user_with_two_connections_listed_once(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await _add(session, 1, "acme/platform")
        await repos.add_connection(
            session,
            owner_tg_id=1,
            base_url=GL,
            scope_path="acme/platform/backend",
            scope_kind="project",
            display_name="backend",
            encrypted_pat=b"x",
            gitlab_user_id=1,
            gitlab_username="gl1",
        )

    async with sessionmaker() as session:
        found = await repos.list_pool_in_scope(
            session, base_url=GL, scope_path="acme/platform"
        )

    assert [u.tg_user_id for u, _ in found] == [1]
