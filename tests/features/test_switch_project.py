"""Экран «Сменить проект»: кнопка появляется от 2 подключений, тап меняет default."""

import pytest
from sqlalchemy.ext.asyncio import create_async_engine

from db import repos
from db.models import Base
from db.session import make_sessionmaker, transaction
from features.onboarding.router import _render_switch, render_welcome
from shared import texts as shared_texts

GL = "https://gl.example.com"


@pytest.fixture
async def sessionmaker():  # type: ignore[no-untyped-def]
    engine = create_async_engine("sqlite+aiosqlite://")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield make_sessionmaker(engine)
    finally:
        await engine.dispose()


async def _seed(session, *scopes: str) -> None:  # type: ignore[no-untyped-def]
    await repos.add_user(
        session, tg_user_id=1, tg_username="u1", is_admin=False, invited_by_tg_id=None
    )
    for s in scopes:
        await repos.add_connection(
            session,
            owner_tg_id=1,
            base_url=GL,
            scope_path=s,
            scope_kind="project",
            display_name=s.rsplit("/", 1)[-1],
            encrypted_pat=b"x",
            gitlab_user_id=1,
            gitlab_username="gl1",
        )


def _labels(markup) -> list[str]:  # type: ignore[no-untyped-def]
    return [b.text for row in markup.inline_keyboard for b in row]


async def test_switch_button_hidden_with_single_connection(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await _seed(session, "grp/only")
        user = await repos.get_user(session, 1)

    _, markup = await render_welcome(user, sessionmaker)
    assert shared_texts.BTN_SWITCH not in _labels(markup)


async def test_switch_button_shown_with_two_connections(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await _seed(session, "grp/a", "grp/b")
        user = await repos.get_user(session, 1)

    _, markup = await render_welcome(user, sessionmaker)
    assert shared_texts.BTN_SWITCH in _labels(markup)


async def test_switch_screen_marks_active_connection(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    async with transaction(sessionmaker) as session:
        await _seed(session, "grp/a", "grp/b")
        conns = await repos.list_connections(session, owner_tg_id=1)
        await repos.set_default_connection(
            session, tg_user_id=1, connection_id=conns[1].id
        )
    async with sessionmaker() as session:
        user = await repos.get_user(session, 1)
    assert user is not None

    _, markup = await _render_switch(user, sessionmaker)
    labels = _labels(markup)
    # ровно один ряд со звездой, и это второе подключение
    starred = [x for x in labels if x.startswith("★")]
    assert len(starred) == 1
    assert "grp/b" in starred[0]


async def test_welcome_reflects_switched_default(sessionmaker) -> None:  # type: ignore[no-untyped-def]
    """Регресс: user из middleware закеширован до записи — меню не должно
    показывать старое подключение после переключения."""
    async with transaction(sessionmaker) as session:
        await _seed(session, "grp/a", "grp/b")
        conns = await repos.list_connections(session, owner_tg_id=1)
        await repos.set_default_connection(
            session, tg_user_id=1, connection_id=conns[0].id
        )
    async with sessionmaker() as session:
        user = await repos.get_user(session, 1)
    assert user is not None

    async with transaction(sessionmaker) as session:
        await repos.set_default_connection(
            session, tg_user_id=1, connection_id=conns[1].id
        )
    user.default_connection_id = conns[1].id  # то же, что делает хендлер

    text, _ = await render_welcome(user, sessionmaker)
    assert "grp/b" in text
    assert "grp/a" not in text
