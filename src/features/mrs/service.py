"""MRs service — GitLab-фасад для MR-роутера.

- Списки/детали — тонкий проброс в ``GitLabClient``.
- ``toggle_self_approve`` / ``merge_mr`` — операции текущего юзера через его
  собственный PAT (без audit-строки: actor == on_behalf_of).
- ``list_proxy_candidates`` / ``perform_proxy_approve`` — approve/unapprove
  от имени чужого подключения; каждое действие пишется в ``approval_events``.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.gitlab import GitLabAuth, GitLabClient
from clients.gitlab.schemas import MrDetail, MrListItem, MrState, ScopeKind
from crypto import PatCrypto
from db import repos
from db.models import Connection
from db.session import transaction


@dataclass(frozen=True, slots=True)
class ProxyToggleResult:
    approved: bool  # true == теперь approved, false == unapproved


@dataclass(slots=True)
class MrsService:
    gitlab: GitLabClient

    async def list_my(
        self, *, auth: GitLabAuth, state: MrState = "opened"
    ) -> list[MrListItem]:
        return await self.gitlab.list_authored_open_mrs(auth, state=state)

    async def list_in_scope(
        self,
        *,
        auth: GitLabAuth,
        scope_path: str,
        scope_kind: ScopeKind,
        state: MrState = "opened",
    ) -> list[MrListItem]:
        return await self.gitlab.list_open_mrs_in_scope(
            auth,
            scope_path=scope_path,
            scope_kind=scope_kind,
            state=state,
        )

    async def get_detail(
        self, *, auth: GitLabAuth, project_id: int, mr_iid: int
    ) -> MrDetail:
        return await self.gitlab.get_mr_detail(
            auth, project_id=project_id, mr_iid=mr_iid
        )

    async def toggle_self_approve(
        self,
        *,
        auth: GitLabAuth,
        connection: Connection,
        project_id: int,
        mr_iid: int,
        sha: str,
    ) -> bool:
        """Toggle approve через PAT самого юзера. Возвращает новое состояние.

        Аудит не пишем: actor == owner. Sha-lock работает через GitLab при
        approve (сервер вернёт 409, если ушёл HEAD).
        """
        approvers = await self.gitlab.list_approvers(
            auth, project_id=project_id, mr_iid=mr_iid
        )
        already = connection.gitlab_user_id in approvers
        if already:
            await self.gitlab.unapprove(auth, project_id=project_id, mr_iid=mr_iid)
            return False
        await self.gitlab.approve(
            auth, project_id=project_id, mr_iid=mr_iid, sha=sha
        )
        return True

    async def merge_mr(
        self, *, auth: GitLabAuth, project_id: int, mr_iid: int, sha: str
    ) -> None:
        await self.gitlab.merge(
            auth, project_id=project_id, mr_iid=mr_iid, sha=sha
        )


@dataclass(slots=True)
class ProxyApprovalService:
    """Approve/unapprove via someone else's connection. Аудитируется в БД."""

    gitlab: GitLabClient
    sessionmaker: async_sessionmaker[AsyncSession]
    crypto: PatCrypto

    async def list_candidates(
        self, *, caller_tg_id: int, base_url: str
    ) -> list[Connection]:
        async with self.sessionmaker() as session:
            return await repos.list_connections_by_base_url(
                session, base_url=base_url, exclude_owner_tg_id=caller_tg_id
            )

    async def get_candidate(
        self, *, connection_id: int, caller_tg_id: int, base_url: str
    ) -> Connection | None:
        """Загрузить candidate и убедиться что он на том же base_url и не наш."""
        async with self.sessionmaker() as session:
            conn = await repos.get_connection(session, connection_id)
        if conn is None:
            return None
        if conn.base_url != base_url or conn.owner_tg_id == caller_tg_id:
            return None
        return conn

    async def toggle(
        self,
        *,
        actor_tg_id: int,
        proxy_conn: Connection,
        project_id: int,
        mr_iid: int,
        sha: str,
    ) -> ProxyToggleResult:
        proxy_auth = GitLabAuth(
            base_url=proxy_conn.base_url,
            pat=self.crypto.decrypt(proxy_conn.encrypted_pat),
        )
        approvers = await self.gitlab.list_approvers(
            proxy_auth, project_id=project_id, mr_iid=mr_iid
        )
        already = proxy_conn.gitlab_user_id in approvers

        if already:
            await self.gitlab.unapprove(
                proxy_auth, project_id=project_id, mr_iid=mr_iid
            )
            action = "unapproved"
            new_state = False
        else:
            await self.gitlab.approve(
                proxy_auth, project_id=project_id, mr_iid=mr_iid, sha=sha
            )
            action = "approved"
            new_state = True

        async with transaction(self.sessionmaker) as session:
            await repos.add_approval_event(
                session,
                actor_tg_id=actor_tg_id,
                on_behalf_of_tg_id=proxy_conn.owner_tg_id,
                connection_id=proxy_conn.id,
                project_id=project_id,
                mr_iid=mr_iid,
                sha=sha,
                action=action,
            )
        return ProxyToggleResult(approved=new_state)
