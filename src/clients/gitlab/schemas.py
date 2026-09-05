from dataclasses import dataclass
from typing import Literal

ScopeKind = Literal["group", "project"]
JobAction = Literal["play", "retry"]
MrState = Literal["opened", "merged", "closed", "all"]


@dataclass(frozen=True, slots=True)
class Project:
    id: int
    path_with_namespace: str
    name: str


@dataclass(frozen=True, slots=True)
class Branch:
    name: str
    default: bool


@dataclass(frozen=True, slots=True)
class Pipeline:
    id: int
    web_url: str
    ref: str
    status: str


@dataclass(frozen=True, slots=True)
class RecentPipeline:
    id: int
    web_url: str
    ref: str
    status: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class Job:
    id: int
    name: str
    stage: str
    status: str
    project_id: int
    action: JobAction = "play"
    project_path: str = ""
    downstream_project_id: int | None = None
    downstream_pipeline_id: int | None = None
    # False — джобу нельзя запустить (running/pending/etc.), показываем read-only.
    playable: bool = True


@dataclass(frozen=True, slots=True)
class PlayedJob:
    id: int
    name: str
    status: str
    web_url: str


@dataclass(frozen=True, slots=True)
class MrListItem:
    project_id: int
    mr_iid: int
    sha: str
    title: str
    web_url: str
    references_full: str
    author_gitlab_id: int
    author_username: str
    author_name: str


@dataclass(frozen=True, slots=True)
class MrDetail:
    project_id: int
    project_name: str
    project_path: str  # path_with_namespace — для аудита и scope-фильтра
    mr_iid: int
    sha: str
    title: str
    web_url: str
    state: str
    description: str
    source_branch: str
    target_branch: str
    draft: bool
    has_conflicts: bool
    author_gitlab_id: int
    author_username: str
    author_name: str
    approver_ids: frozenset[int]
    approver_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class TagInfo:
    name: str
    web_url: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class RecentTag:
    name: str
    commit_sha: str


@dataclass(frozen=True, slots=True)
class PatOwner:
    user_id: int
    username: str


@dataclass(frozen=True, slots=True)
class PipelineNode:
    """Один уровень дерева pipeline: root или downstream (child / multi-project).

    ``label`` — короткое имя для UI (последний сегмент project path).
    """

    project_id: int
    pipeline_id: int
    label: str
    status: str
    web_url: str


@dataclass(frozen=True, slots=True)
class JobRef:
    """Ссылка на конкретную джобу в конкретном pipeline.

    Используется когда мы ищем джобу по имени в дереве (root+downstreams) —
    результат содержит достаточно контекста, чтобы её запустить.
    """

    project_id: int
    pipeline_id: int
    job_id: int
    name: str
    stage: str
    status: str
    node_label: str
