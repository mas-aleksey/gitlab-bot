"""Audit-отчёт: счётчики по типам действий за период + хвост последних событий."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from db import repos
from db.models import User


@dataclass(frozen=True, slots=True)
class ActorStats:
    tg_user_id: int
    username: str | None
    total: int
    by_action: list[tuple[str, int]]  # (action, count), по убыванию count


@dataclass(frozen=True, slots=True)
class RecentEvent:
    occurred_at: datetime
    tg_user_id: int
    username: str | None
    action: str
    target: str


@dataclass(frozen=True, slots=True)
class Scope:
    """Проект/группа активного подключения — по нему режется отчёт."""

    base_url: str
    path: str

    @property
    def key(self) -> tuple[str, str]:
        return self.base_url, self.path


@dataclass(frozen=True, slots=True)
class AuditReport:
    days: int
    actors: list[ActorStats]  # по убыванию total
    recent: list[RecentEvent]
    scope_path: str | None = None

    @property
    def total(self) -> int:
        return sum(a.total for a in self.actors)


@dataclass(slots=True)
class AuditService:
    sessionmaker: async_sessionmaker[AsyncSession]

    async def find_user(self, ref: str) -> User | None:
        """Юзер пула по ``@username`` или по tg_user_id."""
        ref = ref.strip().lstrip("@")
        async with self.sessionmaker() as session:
            if ref.isdigit():
                return await repos.get_user(session, int(ref))
            return await repos.get_user_by_username(session, ref)

    async def scope_of(self, user: User) -> Scope | None:
        """Scope активного подключения. ``None`` — подключения нет."""
        if user.default_connection_id is None:
            return None
        async with self.sessionmaker() as session:
            conn = await repos.get_connection(session, user.default_connection_id)
        if conn is None:
            return None
        return Scope(base_url=conn.base_url, path=conn.scope_path)

    async def report(
        self,
        *,
        days: int = 30,
        recent_limit: int = 10,
        actor_ids: list[int] | None = None,
        scope: Scope | None = None,
    ) -> AuditReport:
        # occurred_at пишется server_default CURRENT_TIMESTAMP — в SQLite это
        # naive UTC. Сравниваем с naive, иначе tz-суффикс ломает текстовое
        # сравнение дат на границе окна.
        since = datetime.now(tz=UTC).replace(tzinfo=None) - timedelta(days=days)
        async with self.sessionmaker() as session:
            scope_key = scope.key if scope is not None else None
            counts = await repos.audit_counts(
                session, since=since, actor_ids=actor_ids, scope=scope_key
            )
            recent = await repos.audit_recent(
                session, limit=recent_limit, actor_ids=actor_ids, scope=scope_key
            )
        return AuditReport(
            days=days,
            scope_path=scope.path if scope is not None else None,
            actors=_group(counts),
            recent=[
                RecentEvent(
                    occurred_at=e.occurred_at,
                    tg_user_id=e.actor_tg_id,
                    username=username,
                    action=e.action,
                    target=e.target,
                )
                for e, username in recent
            ],
        )


def _group(counts: list[tuple[int, str | None, str, int]]) -> list[ActorStats]:
    by_actor: dict[int, tuple[str | None, list[tuple[str, int]]]] = {}
    for tg_user_id, username, action, n in counts:
        _, actions = by_actor.setdefault(tg_user_id, (username, []))
        actions.append((action, n))
    stats = [
        ActorStats(
            tg_user_id=tg_user_id,
            username=username,
            total=sum(n for _, n in actions),
            by_action=sorted(actions, key=lambda p: (-p[1], p[0])),
        )
        for tg_user_id, (username, actions) in by_actor.items()
    ]
    return sorted(stats, key=lambda a: (-a.total, a.tg_user_id))
