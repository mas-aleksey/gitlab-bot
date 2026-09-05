"""Releases service — управление релиз-джобами в tag-пайпах.

Опрос состояния — on-demand (по кнопке «Обновить»), никаких фоновых
задач: MemoryStorage FSM теряет состояние при рестарте, background
watcher оставил бы «висящие» джобы.
"""

from dataclasses import dataclass
from typing import Literal

from clients.gitlab import (
    GitLabAuth,
    GitLabClient,
    GitLabError,
    JobRef,
    PipelineNode,
)
from clients.gitlab.schemas import JobAction, PlayedJob, Project, RecentPipeline, ScopeKind

ReleaseEnv = Literal["test", "uat", "prod"]


@dataclass(frozen=True, slots=True)
class TagPipeline:
    """Pipeline с ref-тегом — карточка в списке /release."""

    id: int
    web_url: str
    ref: str
    status: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class JobRowStats:
    """Агрегат по одному имени джобы во всех downstream дерева.

    Каждое поле — счётчик по статусам:

    * ``ready`` — статус ``manual``, готово к play;
    * ``running`` — pending/running/created/preparing/waiting_for_resource;
    * ``success`` — успешно завершено;
    * ``failed`` — failed/canceled;
    * ``skipped`` — skipped;
    * ``total`` — сколько всего найдено (сумма выше).

    ``playable`` — джобы в статусе ``manual`` (то, что реально запустим).
    ``retryable`` — упавшие/отменённые, их перезапускаем через ``retry``.
    ``stage_order`` — минимальный индекс стадии среди джоб этой группы,
    нужен для стабильной сортировки строк «сверху вниз» по конвейеру.
    """

    name: str
    stage: str
    stage_order: int
    ready: int
    running: int
    success: int
    failed: int
    skipped: int
    total: int
    playable: tuple[JobRef, ...]
    retryable: tuple[JobRef, ...] = ()


@dataclass(frozen=True, slots=True)
class PlayOutcome:
    played: tuple[PlayedJob, ...]
    failed: tuple[tuple[int, str], ...]


@dataclass(slots=True)
class ReleasesService:
    gitlab: GitLabClient

    # ---------- projects & tag pipelines ----------

    async def list_projects(
        self, *, auth: GitLabAuth, scope_path: str, scope_kind: ScopeKind
    ) -> list[Project]:
        return await self.gitlab.list_projects_in_scope(
            auth, scope_path=scope_path, scope_kind=scope_kind
        )

    async def list_tag_pipelines(
        self, *, auth: GitLabAuth, project_id: int, limit: int = 5
    ) -> list[TagPipeline]:
        """Последние pipelines проекта, запущенные на тегах.

        GitLab API ``/pipelines?scope=tags`` уже фильтрует по тегам —
        запрашиваем ровно ``limit`` штук.
        """
        recent = await self._list_recent_scoped(
            auth, project_id=project_id, scope="tags", limit=limit
        )
        return [
            TagPipeline(
                id=r.id,
                web_url=r.web_url,
                ref=r.ref,
                status=r.status,
                updated_at=r.updated_at,
            )
            for r in recent
        ]

    async def _list_recent_scoped(
        self, auth: GitLabAuth, *, project_id: int, scope: str, limit: int
    ) -> list[RecentPipeline]:
        """Копия ``list_recent_pipelines``, но с параметром ``scope``.

        Держим здесь, чтобы не расширять клиент ради одной фичи.
        """
        r = await self.gitlab._get(  # noqa: SLF001 — thin wrapper, see docstring
            auth,
            f"/projects/{project_id}/pipelines",
            params={
                "scope": scope,
                "order_by": "id",
                "sort": "desc",
                "per_page": limit,
                "page": 1,
            },
        )
        data = r.json()
        items: list[RecentPipeline] = []
        for p in data[:limit]:
            items.append(
                RecentPipeline(
                    id=int(p["id"]),
                    web_url=str(p.get("web_url", "")),
                    ref=str(p.get("ref", "")),
                    status=str(p.get("status", "")),
                    updated_at=str(p.get("updated_at", "")),
                )
            )
        return items

    # ---------- pipeline tree & rows ----------

    async def walk_tree(
        self, *, auth: GitLabAuth, project_id: int, pipeline_id: int
    ) -> list[PipelineNode]:
        return await self.gitlab.walk_pipeline_tree(
            auth, project_id=project_id, pipeline_id=pipeline_id
        )

    async def load_rows(
        self, *, auth: GitLabAuth, tree: list[PipelineNode]
    ) -> list[JobRowStats]:
        """Одна строка на каждое уникальное имя джобы, найденное в дереве.

        Один ``/jobs`` на узел (параллельно) — потом группировка в памяти.
        Порядок строк: по минимальному ``stage_order`` появления джобы,
        внутри стадии — по имени. Так экран сверху вниз повторяет конвейер.
        """
        refs = await self.gitlab.list_all_jobs(auth, tree=tree)
        stage_first_seen: dict[str, int] = {}
        for r in refs:
            stage_first_seen.setdefault(r.stage, len(stage_first_seen))

        grouped: dict[str, list[JobRef]] = {}
        for r in refs:
            grouped.setdefault(r.name, []).append(r)

        rows: list[JobRowStats] = []
        for name, items in grouped.items():
            rows.append(_aggregate(name, items, stage_first_seen))
        rows.sort(key=lambda r: (r.stage_order, r.name))
        return rows

    async def play_refs(
        self,
        *,
        auth: GitLabAuth,
        refs: tuple[JobRef, ...],
        action: JobAction = "play",
    ) -> PlayOutcome:
        """Запустить (``play``) или перезапустить (``retry``) переданные refs.

        Роутер обязан передать список refs из свежего ``load_rows`` — если
        UI устарел и джоба уже не в ожидаемом статусе, GitLab вернёт 400 →
        уйдёт в ``failed``.
        """
        played: list[PlayedJob] = []
        failed: list[tuple[int, str]] = []
        for ref in refs:
            try:
                p = await self.gitlab.play_job(
                    auth,
                    project_id=ref.project_id,
                    job_id=ref.job_id,
                    action=action,
                )
                played.append(p)
            except GitLabError as e:
                failed.append((ref.job_id, str(e)))
        return PlayOutcome(played=tuple(played), failed=tuple(failed))


def _aggregate(
    name: str, refs: list[JobRef], stage_order: dict[str, int]
) -> JobRowStats:
    ready = 0
    running = 0
    success = 0
    failed = 0
    skipped = 0
    playable: list[JobRef] = []
    retryable: list[JobRef] = []
    # Стадия джобы одна и та же для одного name в правильно устроенном
    # пайплайне — но на всякий берём минимальный stage_order.
    min_stage_idx = min(stage_order.get(r.stage, 0) for r in refs)
    stage = next((r.stage for r in refs if stage_order.get(r.stage, 0) == min_stage_idx), "")
    for r in refs:
        s = r.status
        if s == "manual":
            ready += 1
            playable.append(r)
        elif s in {"running", "pending", "created", "preparing", "waiting_for_resource"}:
            running += 1
        elif s == "success":
            success += 1
        elif s in {"failed", "canceled"}:
            failed += 1
            retryable.append(r)
        elif s == "skipped":
            skipped += 1
    return JobRowStats(
        name=name,
        stage=stage,
        stage_order=min_stage_idx,
        ready=ready,
        running=running,
        success=success,
        failed=failed,
        skipped=skipped,
        total=len(refs),
        playable=tuple(playable),
        retryable=tuple(retryable),
    )
