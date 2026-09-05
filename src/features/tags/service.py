"""Tags service — тонкий фасад над ``GitLabClient`` для tags-роутера.

Функции: list проектов в scope, list веток (для default-ref), list последних
тегов, create tag. Все — прямой проброс в клиент.
"""

from dataclasses import dataclass

from clients.gitlab import GitLabAuth, GitLabClient
from clients.gitlab.schemas import Branch, Project, RecentTag, ScopeKind, TagInfo


@dataclass(slots=True)
class TagsService:
    gitlab: GitLabClient

    async def list_projects(
        self, *, auth: GitLabAuth, scope_path: str, scope_kind: ScopeKind
    ) -> list[Project]:
        return await self.gitlab.list_projects_in_scope(
            auth, scope_path=scope_path, scope_kind=scope_kind
        )

    async def list_branches(self, *, auth: GitLabAuth, project_id: int) -> list[Branch]:
        return await self.gitlab.list_branches(auth, project_id=project_id)

    async def list_recent(
        self, *, auth: GitLabAuth, project_id: int, limit: int = 10
    ) -> list[RecentTag]:
        return await self.gitlab.list_recent_tags(
            auth, project_id=project_id, limit=limit
        )

    async def create(
        self, *, auth: GitLabAuth, project_id: int, tag_name: str, ref: str
    ) -> TagInfo:
        return await self.gitlab.create_tag(
            auth, project_id=project_id, tag_name=tag_name, ref=ref
        )
