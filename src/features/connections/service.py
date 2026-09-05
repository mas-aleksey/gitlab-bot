"""Connections service — validate PAT + scope against GitLab, then persist.

Registration steps (all inside one transaction):

1. Ask GitLab who owns the PAT (``resolve_pat_owner``) — surfaces 401 as
   :class:`clients.gitlab.GitLabAuthError`.
2. Probe whether ``scope_path`` is a group or a project.
3. Encrypt the PAT.
4. INSERT the connection; make it default if the user has none.

The unique constraint ``(owner_tg_id, base_url, scope_path)`` catches
duplicates at the DB level and is translated to :class:`DuplicateConnection`.
"""

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.gitlab import GitLabAuth, GitLabClient
from crypto import PatCrypto
from db import repos
from db.models import Connection
from db.session import transaction


class ConnectionError(Exception):
    """Base for connection-service failures."""


class DuplicateConnection(ConnectionError):
    """A connection with the same (base_url, scope_path) already exists."""


class ConnectionNotFound(ConnectionError):
    """The connection id doesn't exist or doesn't belong to this owner."""


class PatOwnerMismatch(ConnectionError):
    """New PAT belongs to a different GitLab user than the existing one."""


@dataclass(frozen=True, slots=True)
class RegisteredConnection:
    connection: Connection
    made_default: bool


@dataclass(frozen=True, slots=True)
class ConnectionsView:
    items: list[Connection]
    default_id: int | None


@dataclass(frozen=True, slots=True)
class DeletedConnection:
    was_default: bool
    new_default: Connection | None


class ConnectionsService:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        gitlab: GitLabClient,
        crypto: PatCrypto,
    ) -> None:
        self._sm = sessionmaker
        self._gitlab = gitlab
        self._crypto = crypto

    async def register(
        self,
        *,
        owner_tg_id: int,
        base_url: str,
        scope_path: str,
        display_name: str,
        pat: str,
    ) -> RegisteredConnection:
        """Validate against GitLab, encrypt PAT, persist. Sets default if first."""
        auth = GitLabAuth(base_url=base_url, pat=pat)
        owner = await self._gitlab.resolve_pat_owner(auth)
        scope_kind = await self._gitlab.resolve_scope_kind(auth, scope_path)
        encrypted = self._crypto.encrypt(pat)

        async with transaction(self._sm) as session:
            existing = await repos.list_connections(session, owner_tg_id=owner_tg_id)
            try:
                conn = await repos.add_connection(
                    session,
                    owner_tg_id=owner_tg_id,
                    base_url=base_url,
                    scope_path=scope_path,
                    scope_kind=scope_kind,
                    display_name=display_name,
                    encrypted_pat=encrypted,
                    gitlab_user_id=owner.user_id,
                    gitlab_username=owner.username,
                )
            except IntegrityError as e:
                raise DuplicateConnection() from e

            make_default = not existing
            if make_default:
                await repos.set_default_connection(
                    session, tg_user_id=owner_tg_id, connection_id=conn.id
                )
            logger.info(
                "connection registered: id={} owner={} scope={} default={}",
                conn.id,
                owner_tg_id,
                scope_path,
                make_default,
            )
            return RegisteredConnection(connection=conn, made_default=make_default)

    async def list_for_user(self, *, owner_tg_id: int) -> ConnectionsView:
        async with self._sm() as session:
            items = await repos.list_connections(session, owner_tg_id=owner_tg_id)
            user = await repos.get_user(session, owner_tg_id)
            default_id = user.default_connection_id if user is not None else None
        return ConnectionsView(items=items, default_id=default_id)

    async def get_owned(
        self, *, owner_tg_id: int, connection_id: int
    ) -> Connection:
        async with self._sm() as session:
            conn = await repos.get_connection(session, connection_id)
        if conn is None or conn.owner_tg_id != owner_tg_id:
            raise ConnectionNotFound()
        return conn

    async def set_default(self, *, owner_tg_id: int, connection_id: int) -> Connection:
        async with transaction(self._sm) as session:
            conn = await repos.get_connection(session, connection_id)
            if conn is None or conn.owner_tg_id != owner_tg_id:
                raise ConnectionNotFound()
            await repos.set_default_connection(
                session, tg_user_id=owner_tg_id, connection_id=connection_id
            )
            return conn

    async def delete(self, *, owner_tg_id: int, connection_id: int) -> DeletedConnection:
        """Delete connection. If it was default — auto-pick the next one (or None)."""
        async with transaction(self._sm) as session:
            conn = await repos.get_connection(session, connection_id)
            if conn is None or conn.owner_tg_id != owner_tg_id:
                raise ConnectionNotFound()
            user = await repos.get_user(session, owner_tg_id)
            was_default = (
                user is not None and user.default_connection_id == connection_id
            )
            if was_default:
                await repos.set_default_connection(
                    session, tg_user_id=owner_tg_id, connection_id=None
                )
            await repos.delete_connection(session, connection_id=connection_id)

            new_default: Connection | None = None
            if was_default:
                remaining = await repos.list_connections(
                    session, owner_tg_id=owner_tg_id
                )
                if remaining:
                    new_default = remaining[0]
                    await repos.set_default_connection(
                        session,
                        tg_user_id=owner_tg_id,
                        connection_id=new_default.id,
                    )
            return DeletedConnection(was_default=was_default, new_default=new_default)

    async def rotate_pat(
        self, *, owner_tg_id: int, connection_id: int, new_pat: str
    ) -> Connection:
        """Validate new PAT against GitLab, ensure same owner, persist."""
        existing = await self.get_owned(
            owner_tg_id=owner_tg_id, connection_id=connection_id
        )
        auth = GitLabAuth(base_url=existing.base_url, pat=new_pat)
        owner = await self._gitlab.resolve_pat_owner(auth)
        if owner.user_id != existing.gitlab_user_id:
            raise PatOwnerMismatch(
                f"PAT belongs to {owner.username}, expected {existing.gitlab_username}"
            )
        encrypted = self._crypto.encrypt(new_pat)
        async with transaction(self._sm) as session:
            updated = await repos.update_connection_pat(
                session,
                connection_id=connection_id,
                encrypted_pat=encrypted,
                gitlab_user_id=owner.user_id,
                gitlab_username=owner.username,
            )
            if updated is None:
                raise ConnectionNotFound()
            return updated
