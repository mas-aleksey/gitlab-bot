"""Pipelines service — тонкая обёртка над ``GitLabClient`` для хендлеров.

Отвечает за: список проектов в scope подключения, список веток проекта,
недавние pipelines проекта, создание pipeline на ветке, список playable
джоб pipeline и их запуск (play/retry).
"""

from dataclasses import dataclass

from clients.gitlab import GitLabAuth, GitLabClient, GitLabError
from clients.gitlab.schemas import (
    Branch,
    Job,
    JobAction,
    Pipeline,
    PlayedJob,
    Project,
    RecentPipeline,
    ScopeKind,
)


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    """Итог batch-запуска джоб. ``failed`` — (job_id, reason)."""

    played: tuple[PlayedJob, ...]
    failed: tuple[tuple[int, str], ...]


@dataclass(slots=True)
class PipelinesService:
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
    ) -> list[RecentPipeline]:
        return await self.gitlab.list_recent_pipelines(
            auth, project_id=project_id, limit=limit
        )

    async def create(
        self, *, auth: GitLabAuth, project_id: int, ref: str
    ) -> Pipeline:
        return await self.gitlab.create_pipeline(auth, project_id=project_id, ref=ref)

    async def list_playable_jobs(
        self, *, auth: GitLabAuth, project_id: int, pipeline_id: int
    ) -> list[Job]:
        """Playable джобы + bridge-placeholder-ы **одного уровня** pipeline.

        Каждый bridge (``downstream_pipeline_id is not None``) — это ссылка
        на дочерний pipeline. Drill в него делает handler через отдельный
        вызов этого же метода с (downstream_project_id, downstream_pipeline_id).
        Не разворачиваем дерево одним запросом — иначе на больших pipelines
        клавиатура превысит лимит Telegram.
        """
        return await self.gitlab.list_playable_jobs(
            auth, project_id=project_id, pipeline_id=pipeline_id
        )

    async def play_jobs(
        self,
        *,
        auth: GitLabAuth,
        refs: list[tuple[int, int, JobAction]],
    ) -> PlayOutcome:
        """Запуск списка джоб. ``refs`` — (project_id, job_id, action).

        Пробуем каждую независимо: одна ошибка не валит остальные.
        """
        played: list[PlayedJob] = []
        failed: list[tuple[int, str]] = []
        for project_id, job_id, action in refs:
            try:
                p = await self.gitlab.play_job(
                    auth, project_id=project_id, job_id=job_id, action=action
                )
                played.append(p)
            except GitLabError as e:
                failed.append((job_id, str(e)))
        return PlayOutcome(played=tuple(played), failed=tuple(failed))
