"""Thin repository functions over AsyncSession.

Kept as free functions (not classes) — services compose them directly.
"""

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import and_, func, literal, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from db.models import ApprovalEvent, AuditEvent, Connection, UsedInviteCode, User

# ---------- users ----------

async def users_count(session: AsyncSession) -> int:
    r = await session.scalar(select(func.count()).select_from(User))
    return int(r or 0)


async def get_user(session: AsyncSession, tg_user_id: int) -> User | None:
    return await session.get(User, tg_user_id)


async def add_user(
    session: AsyncSession,
    *,
    tg_user_id: int,
    tg_username: str | None,
    is_admin: bool,
    invited_by_tg_id: int | None,
) -> User:
    user = User(
        tg_user_id=tg_user_id,
        tg_username=tg_username,
        is_admin=is_admin,
        invited_by_tg_id=invited_by_tg_id,
    )
    session.add(user)
    await session.flush()
    return user


async def set_default_connection(
    session: AsyncSession, *, tg_user_id: int, connection_id: int | None
) -> None:
    user = await session.get(User, tg_user_id)
    if user is None:
        raise LookupError(f"user {tg_user_id} not found")
    user.default_connection_id = connection_id


async def list_pool_in_scope(
    session: AsyncSession, *, base_url: str, scope_path: str
) -> list[tuple[User, str]]:
    """Участники pool, чей scope на том же инстансе пересекается с ``scope_path``.

    Пересечение = один путь вложен в другой по границе сегмента: подключённый
    к группе ``a/b`` видит подключённого к проекту ``a/b/c`` и наоборот, но
    ``a/bc`` — чужой. Возвращает (user, scope_path совпавшего подключения);
    у юзера с несколькими подходящими подключениями берётся первое.
    """
    rows = await session.execute(
        select(User, Connection.scope_path)
        .join(Connection, Connection.owner_tg_id == User.tg_user_id)
        .where(
            Connection.base_url == base_url,
            or_(
                Connection.scope_path == scope_path,
                Connection.scope_path.startswith(f"{scope_path}/"),
                literal(scope_path).startswith(Connection.scope_path + "/"),
            ),
        )
        .order_by(User.registered_at, Connection.created_at)
    )
    seen: set[int] = set()
    out: list[tuple[User, str]] = []
    for user, matched_scope in rows.all():
        if user.tg_user_id in seen:
            continue
        seen.add(user.tg_user_id)
        out.append((user, matched_scope))
    return out


async def get_user_by_username(session: AsyncSession, username: str) -> User | None:
    user: User | None = await session.scalar(
        select(User).where(func.lower(User.tg_username) == username.lower())
    )
    return user


async def list_pool(session: AsyncSession) -> list[User]:
    r = await session.scalars(select(User).order_by(User.registered_at))
    return list(r)


# ---------- connections ----------

async def add_connection(
    session: AsyncSession,
    *,
    owner_tg_id: int,
    base_url: str,
    scope_path: str,
    scope_kind: str,
    display_name: str,
    encrypted_pat: bytes,
    gitlab_user_id: int,
    gitlab_username: str,
) -> Connection:
    conn = Connection(
        owner_tg_id=owner_tg_id,
        base_url=base_url,
        scope_path=scope_path,
        scope_kind=scope_kind,
        display_name=display_name,
        encrypted_pat=encrypted_pat,
        gitlab_user_id=gitlab_user_id,
        gitlab_username=gitlab_username,
    )
    session.add(conn)
    await session.flush()
    return conn


async def get_connection(session: AsyncSession, connection_id: int) -> Connection | None:
    return await session.get(Connection, connection_id)


async def list_connections(session: AsyncSession, *, owner_tg_id: int) -> list[Connection]:
    r = await session.scalars(
        select(Connection)
        .where(Connection.owner_tg_id == owner_tg_id)
        .order_by(Connection.created_at)
    )
    return list(r)


async def list_connections_by_base_url(
    session: AsyncSession, *, base_url: str, exclude_owner_tg_id: int
) -> list[Connection]:
    """Все connections на том же GitLab-инстансе, кроме принадлежащих caller-у.

    Дедуплицируем по ``owner_tg_id``: у одного юзера может быть несколько
    подключений на одном инстансе (разные scope), но GitLab-user у него один —
    для approve-as релевантен любой из его PAT-ов.
    """
    r = await session.scalars(
        select(Connection)
        .where(
            Connection.base_url == base_url,
            Connection.owner_tg_id != exclude_owner_tg_id,
        )
        .order_by(Connection.owner_tg_id, Connection.created_at)
    )
    seen: set[int] = set()
    out: list[Connection] = []
    for c in r:
        if c.owner_tg_id in seen:
            continue
        seen.add(c.owner_tg_id)
        out.append(c)
    return out


async def delete_connection(session: AsyncSession, *, connection_id: int) -> None:
    conn = await session.get(Connection, connection_id)
    if conn is not None:
        await session.delete(conn)


async def update_connection_pat(
    session: AsyncSession,
    *,
    connection_id: int,
    encrypted_pat: bytes,
    gitlab_user_id: int,
    gitlab_username: str,
) -> Connection | None:
    conn = await session.get(Connection, connection_id)
    if conn is None:
        return None
    conn.encrypted_pat = encrypted_pat
    conn.gitlab_user_id = gitlab_user_id
    conn.gitlab_username = gitlab_username
    return conn


# ---------- invite codes ----------

async def is_code_used(session: AsyncSession, code: str) -> bool:
    r = await session.scalar(select(UsedInviteCode.id).where(UsedInviteCode.code == code))
    return r is not None


async def mark_code_used(
    session: AsyncSession,
    *,
    code: str,
    inviter_tg_id: int,
    consumer_tg_id: int,
) -> None:
    session.add(
        UsedInviteCode(
            code=code,
            inviter_tg_id=inviter_tg_id,
            consumer_tg_id=consumer_tg_id,
        )
    )
    await session.flush()


# ---------- approval events ----------

async def add_approval_event(
    session: AsyncSession,
    *,
    actor_tg_id: int,
    on_behalf_of_tg_id: int,
    connection_id: int,
    project_id: int,
    mr_iid: int,
    sha: str,
    action: str,
) -> None:
    session.add(
        ApprovalEvent(
            actor_tg_id=actor_tg_id,
            on_behalf_of_tg_id=on_behalf_of_tg_id,
            connection_id=connection_id,
            project_id=project_id,
            mr_iid=mr_iid,
            sha=sha,
            action=action,
        )
    )
    await session.flush()


# ---------- audit ----------

async def add_audit_event(
    session: AsyncSession,
    *,
    actor_tg_id: int,
    action: str,
    target: str,
    base_url: str | None = None,
    project_path: str | None = None,
) -> None:
    session.add(
        AuditEvent(
            actor_tg_id=actor_tg_id,
            action=action,
            target=target,
            base_url=base_url,
            project_path=project_path,
        )
    )
    await session.flush()


def _in_scope(base_url: str, scope_path: str) -> ColumnElement[bool]:
    """Событие относится к scope: тот же инстанс + путь вложен в scope.

    Вложенность — по границе сегмента, как в ``list_pool_in_scope``:
    scope ``a/b`` покрывает событие в ``a/b/c``, но не в ``a/bc``. Обратное
    направление нужно для действий над подключением, где ``project_path`` —
    сам scope-путь (группа), а спрашивают про вложенный проект.
    """
    return and_(
        AuditEvent.base_url == base_url,
        or_(
            AuditEvent.project_path == scope_path,
            AuditEvent.project_path.startswith(f"{scope_path}/"),
            literal(scope_path).startswith(AuditEvent.project_path + "/"),
        ),
    )


async def audit_counts(
    session: AsyncSession,
    *,
    since: datetime,
    actor_ids: Sequence[int] | None = None,
    scope: tuple[str, str] | None = None,
) -> list[tuple[int, str | None, str, int]]:
    """(actor_tg_id, tg_username, action, count) за период, по убыванию count."""
    q = (
        select(
            AuditEvent.actor_tg_id,
            User.tg_username,
            AuditEvent.action,
            func.count().label("n"),
        )
        .join(User, User.tg_user_id == AuditEvent.actor_tg_id, isouter=True)
        .where(AuditEvent.occurred_at >= since)
        .group_by(AuditEvent.actor_tg_id, AuditEvent.action)
        .order_by(func.count().desc())
    )
    if actor_ids is not None:
        q = q.where(AuditEvent.actor_tg_id.in_(actor_ids))
    if scope is not None:
        q = q.where(_in_scope(*scope))
    rows = await session.execute(q)
    return [(int(a), u, act, int(n)) for a, u, act, n in rows.all()]


async def audit_recent(
    session: AsyncSession,
    *,
    limit: int,
    actor_ids: Sequence[int] | None = None,
    scope: tuple[str, str] | None = None,
) -> list[tuple[AuditEvent, str | None]]:
    q = (
        select(AuditEvent, User.tg_username)
        .join(User, User.tg_user_id == AuditEvent.actor_tg_id, isouter=True)
        .order_by(AuditEvent.occurred_at.desc(), AuditEvent.id.desc())
        .limit(limit)
    )
    if actor_ids is not None:
        q = q.where(AuditEvent.actor_tg_id.in_(actor_ids))
    if scope is not None:
        q = q.where(_in_scope(*scope))
    rows = await session.execute(q)
    return [(e, u) for e, u in rows.all()]
