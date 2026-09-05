"""``/release`` — управление релизами: drill в tag-пайп → play релиз-джоб.

Flow: project picker → список последних 5 tag-пайпов → drill в один →
экран со строкой-кнопкой на каждое уникальное имя джобы во всех
downstream. Клик по кнопке ready-джобы → confirm → batch play →
обновление. Клик по неактивной кнопке — no-op (виден статус).

Кеш дерева + строк (name → playable refs) — в FSM ``state.get_data()``,
чтобы «Обновить» работал явно, без фоновых задач.
"""

from typing import cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.gitlab import GitLabAuth, JobRef, PipelineNode
from clients.gitlab.schemas import JobAction, Project, ScopeKind
from db.models import Connection
from features.releases import texts as t
from features.releases.service import (
    JobRowStats,
    ReleasesService,
    TagPipeline,
)
from shared import texts as shared_texts
from shared.audit import log_action
from shared.edit import edit_or_answer
from shared.keyboards import add_main_menu
from shared.middlewares import ConnectionMiddleware

router = Router(name="releases")
router.message.middleware(ConnectionMiddleware())
router.callback_query.middleware(ConnectionMiddleware())


_PAGE_SIZE = 8
_TAG_LIMIT = 5

_PROJECTS_KEY = "rel_projects"
_PROJ_KEY = "rel_proj_id"
_PIPES_KEY = "rel_pipes"
_PIPE_KEY = "rel_pipe"  # {"id", "ref", "web_url"}
_TREE_KEY = "rel_tree"
_ROWS_KEY = "rel_rows"  # [{"name", "ready", ..., "playable": [refs]}]


class RelCB(CallbackData, prefix="rel"):
    # actions:
    #   projects_page — pagination
    #   pick_proj     — user picked a project → tag pipelines list
    #   refresh_pipes — reload tag pipelines list
    #   pick_pipe     — drill into a tag pipeline (arg = pipeline index)
    #   refresh_pipe  — reload tree+rows for current pipeline
    #   confirm_play  — show confirm for job row index (arg = row idx)
    #   play          — actually play manual jobs of row by index (arg = row idx)
    #   retry         — retry failed/canceled jobs of row by index (arg = row idx)
    #   noop          — button click for row with nothing to do
    action: str
    arg: int = 0


# ---------- /release entrypoint ----------


@router.message(Command("release"))
async def on_release(
    message: Message,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> None:
    await state.clear()
    text, markup = await _entry(
        state=state, connection=connection, auth=auth, releases=releases
    )
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == shared_texts.NAV_RELEASES_CB)
async def on_nav_release(
    callback: CallbackQuery,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> None:
    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = await _entry(
        state=state, connection=connection, auth=auth, releases=releases
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


async def _entry(
    *,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> tuple[str, InlineKeyboardMarkup | None]:
    projects = await releases.list_projects(
        auth=auth,
        scope_path=connection.scope_path,
        scope_kind=cast(ScopeKind, connection.scope_kind),
    )
    if not projects:
        kb = InlineKeyboardBuilder()
        add_main_menu(kb)
        return t.REL_PROJECTS_EMPTY, kb.as_markup()

    await _cache_projects(state, projects)

    if len(projects) == 1:
        await state.update_data({_PROJ_KEY: projects[0].id})
        pipes = await releases.list_tag_pipelines(
            auth=auth, project_id=projects[0].id, limit=_TAG_LIMIT
        )
        await _cache_pipes(state, pipes)
        return _pipes_view(projects[0], pipes)

    return (
        t.REL_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=0),
    )


@router.callback_query(RelCB.filter(F.action == "projects_page"))
async def on_projects_page(
    callback: CallbackQuery,
    callback_data: RelCB,
    state: FSMContext,
    connection: Connection,
) -> None:
    projects = await _load_projects(state)
    if projects is None:
        await callback.answer()
        return
    await edit_or_answer(
        callback,
        t.REL_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=callback_data.arg),
    )
    await callback.answer()


@router.callback_query(RelCB.filter(F.action.in_({"pick_proj", "refresh_pipes"})))
async def on_pick_proj(
    callback: CallbackQuery,
    callback_data: RelCB,
    state: FSMContext,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    await state.update_data({_PROJ_KEY: project.id})
    pipes = await releases.list_tag_pipelines(
        auth=auth, project_id=project.id, limit=_TAG_LIMIT
    )
    await _cache_pipes(state, pipes)
    text, markup = _pipes_view(project, pipes)
    await edit_or_answer(callback, text, markup)
    await callback.answer()


# ---------- drill into a pipeline ----------


@router.callback_query(RelCB.filter(F.action == "pick_pipe"))
async def on_pick_pipe(
    callback: CallbackQuery,
    callback_data: RelCB,
    state: FSMContext,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    pipes = await _load_pipes(state)
    if project is None or not 0 <= callback_data.arg < len(pipes):
        await callback.answer()
        return
    pipe = pipes[callback_data.arg]
    await state.update_data(
        {
            _PIPE_KEY: {
                "id": pipe.id,
                "ref": pipe.ref,
                "web_url": pipe.web_url,
            }
        }
    )
    await _render_pipe(
        callback=callback,
        state=state,
        auth=auth,
        releases=releases,
        project=project,
        pipeline_id=pipe.id,
        reload_tree=True,
    )


@router.callback_query(RelCB.filter(F.action == "refresh_pipe"))
async def on_refresh_pipe(
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    releases: ReleasesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    pipe = data.get(_PIPE_KEY)
    if project is None or not isinstance(pipe, dict):
        await callback.answer()
        return
    await _render_pipe(
        callback=callback,
        state=state,
        auth=auth,
        releases=releases,
        project=project,
        pipeline_id=int(pipe["id"]),
        reload_tree=True,
    )


# ---------- confirm + play ----------


@router.callback_query(RelCB.filter(F.action == "confirm_play"))
async def on_confirm_play(
    callback: CallbackQuery,
    callback_data: RelCB,
    state: FSMContext,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    pipe = data.get(_PIPE_KEY)
    rows = _rows_from_state(data)
    idx = callback_data.arg
    if (
        project is None
        or not isinstance(pipe, dict)
        or not 0 <= idx < len(rows)
    ):
        await callback.answer()
        return
    row = rows[idx]
    if row.ready == 0 and row.failed == 0:
        await callback.answer(t.REL_PLAY_NONE, show_alert=True)
        return

    kb = InlineKeyboardBuilder()
    if row.ready > 0:
        kb.button(
            text=t.REL_BTN_CONFIRM.format(n=row.ready),
            callback_data=RelCB(action="play", arg=idx).pack(),
        )
    if row.failed > 0:
        kb.button(
            text=t.REL_BTN_CONFIRM_RETRY.format(n=row.failed),
            callback_data=RelCB(action="retry", arg=idx).pack(),
        )
    kb.button(
        text=t.REL_BTN_CANCEL,
        callback_data=RelCB(action="refresh_pipe").pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        t.REL_CONFIRM_HEADER.format(
            project_name=project.name,
            ref=str(pipe.get("ref", "")),
            name=row.name,
            ready=row.ready,
            failed=row.failed,
        ),
        kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(RelCB.filter(F.action.in_({"play", "retry"})))
async def on_play(
    callback: CallbackQuery,
    callback_data: RelCB,
    state: FSMContext,
    auth: GitLabAuth,
    releases: ReleasesService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    pipe = data.get(_PIPE_KEY)
    rows = _rows_from_state(data)
    idx = callback_data.arg
    if (
        project is None
        or not isinstance(pipe, dict)
        or not 0 <= idx < len(rows)
    ):
        await callback.answer()
        return
    row = rows[idx]
    action: JobAction = "retry" if callback_data.action == "retry" else "play"
    refs = row.retryable if action == "retry" else row.playable
    if not refs:
        await callback.answer(
            t.REL_RETRY_NONE if action == "retry" else t.REL_PLAY_NONE,
            show_alert=True,
        )
        await _render_pipe(
            callback=callback,
            state=state,
            auth=auth,
            releases=releases,
            project=project,
            pipeline_id=int(pipe["id"]),
            reload_tree=True,
        )
        return

    outcome = await releases.play_refs(auth=auth, refs=refs, action=action)
    logger.info(
        "release {}: project={} pipeline={} name={} started={} failed={}",
        action,
        project.path_with_namespace,
        pipe.get("id"),
        row.name,
        len(outcome.played),
        len(outcome.failed),
    )

    if outcome.played:
        await log_action(
            sessionmaker,
            actor_tg_id=callback.from_user.id,
            action=f"release.{action}",
            target=f"{project.path_with_namespace} {row.name} ×{len(outcome.played)}",
            base_url=auth.base_url,
            project_path=project.path_with_namespace,
        )

    parts = [t.REL_PLAY_OK.format(n=len(outcome.played))]
    if outcome.failed:
        parts.append(t.REL_PLAY_FAIL.format(n=len(outcome.failed)))
        for jid, reason in outcome.failed:
            parts.append(f"\n• #{jid}: {reason}")
    await callback.answer("".join(parts)[:200], show_alert=True)

    await _render_pipe(
        callback=callback,
        state=state,
        auth=auth,
        releases=releases,
        project=project,
        pipeline_id=int(pipe["id"]),
        reload_tree=True,
    )


@router.callback_query(RelCB.filter(F.action == "noop"))
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ---------- rendering ----------


def _project_header(project: Project) -> str:
    return f"<b>{project.name}</b>\n<code>{project.path_with_namespace}</code>"


def _pipes_view(
    project: Project, pipes: list[TagPipeline]
) -> tuple[str, InlineKeyboardMarkup]:
    header = _project_header(project) + "\n\n"
    if not pipes:
        body = t.REL_TAG_LIST_EMPTY
    else:
        body = t.REL_TAG_LIST_HEADER.format(project_name=project.name)

    kb = InlineKeyboardBuilder()
    for idx, p in enumerate(pipes):
        label = f"{_pipe_status_icon(p.status)} {p.ref} — {p.status}"
        if len(label) > 64:
            label = label[:61] + "..."
        kb.button(
            text=label,
            callback_data=RelCB(action="pick_pipe", arg=idx).pack(),
        )
    kb.button(
        text=t.REL_BTN_REFRESH,
        callback_data=RelCB(action="refresh_pipes", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    return header + body, kb.as_markup()


async def _render_pipe(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    releases: ReleasesService,
    project: Project,
    pipeline_id: int,
    reload_tree: bool,
) -> None:
    if reload_tree:
        tree = await releases.walk_tree(
            auth=auth, project_id=project.id, pipeline_id=pipeline_id
        )
        await state.update_data({_TREE_KEY: [_node_to_dict(n) for n in tree]})
    else:
        data = await state.get_data()
        tree = _tree_from_state(data)

    data = await state.get_data()
    pipe = data.get(_PIPE_KEY) or {}
    pipe_ref = str(pipe.get("ref", ""))
    pipe_url = str(pipe.get("web_url", ""))

    if not tree:
        await state.update_data({_ROWS_KEY: []})
        kb = InlineKeyboardBuilder()
        kb.button(
            text=t.REL_BTN_REFRESH,
            callback_data=RelCB(action="refresh_pipe").pack(),
        )
        kb.adjust(1)
        add_main_menu(kb)
        await edit_or_answer(
            callback,
            _project_header(project) + "\n\n" + t.REL_PIPE_TREE_EMPTY,
            kb.as_markup(),
        )
        await callback.answer()
        return

    rows = await releases.load_rows(auth=auth, tree=tree)
    await state.update_data({_ROWS_KEY: [_row_to_dict(r) for r in rows]})

    header = t.REL_PIPE_HEADER.format(
        project_name=project.name,
        ref=pipe_ref,
        web_url=pipe_url,
        id=pipeline_id,
        downstream_count=max(0, len(tree) - 1),
    )
    body = t.REL_PIPE_LEGEND if rows else t.REL_PIPE_NO_JOBS

    kb = InlineKeyboardBuilder()
    current_stage: str | None = None
    for idx, row in enumerate(rows):
        # Разделитель-строка между стадиями (текстовая кнопка с noop).
        if row.stage != current_stage:
            current_stage = row.stage
            if row.stage:
                kb.button(
                    text=t.REL_STAGE_HEADER.format(stage=row.stage),
                    callback_data=RelCB(action="noop").pack(),
                )
        action = "confirm_play" if row.ready > 0 or row.failed > 0 else "noop"
        kb.button(
            text=_row_button(row),
            callback_data=RelCB(action=action, arg=idx).pack(),
        )
    kb.button(
        text=t.REL_BTN_REFRESH,
        callback_data=RelCB(action="refresh_pipe").pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)

    await edit_or_answer(callback, header + body, kb.as_markup())
    await callback.answer()


def _row_button(row: JobRowStats) -> str:
    icon = _row_icon(row)
    if row.ready > 0:
        prefix = t.REL_BTN_PLAY_PREFIX
    elif row.failed > 0:
        prefix = t.REL_BTN_RETRY_PREFIX
    else:
        prefix = ""
    counts = t.REL_COUNTS_FMT.format(
        success=row.success,
        running=row.running,
        ready=row.ready,
        failed=row.failed,
        skipped=row.skipped,
    )
    return f"{prefix}{icon} {row.name} · {counts}"


def _row_icon(row: JobRowStats) -> str:
    if row.failed > 0:
        return "❌"
    if row.running > 0:
        return "🏃"
    if row.ready > 0:
        return "✋"
    if row.total > 0 and row.success + row.skipped == row.total:
        return "✅"
    return "⏳"


def _pipe_status_icon(status: str) -> str:
    return {
        "success": "✅",
        "failed": "❌",
        "canceled": "🚫",
        "skipped": "⏭",
        "running": "🏃",
        "pending": "⏳",
        "created": "🆕",
        "manual": "✋",
        "waiting_for_resource": "⏳",
        "preparing": "⏳",
        "scheduled": "🗓",
    }.get(status, "•")


def _projects_keyboard(
    projects: list[Project], *, page: int
) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(projects) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    slice_ = projects[start : start + _PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for p in slice_:
        kb.button(
            text=p.name,
            callback_data=RelCB(action="pick_proj", arg=p.id).pack(),
        )
    kb.adjust(1)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="‹ Назад",
                    callback_data=RelCB(action="projects_page", arg=page - 1).pack(),
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="Вперёд ›",
                    callback_data=RelCB(action="projects_page", arg=page + 1).pack(),
                )
            )
        if nav:
            kb.row(*nav)

    add_main_menu(kb)
    return kb.as_markup()


# ---------- state helpers ----------


async def _cache_projects(state: FSMContext, projects: list[Project]) -> None:
    await state.update_data(
        {
            _PROJECTS_KEY: [
                {"id": p.id, "name": p.name, "path": p.path_with_namespace}
                for p in projects
            ]
        }
    )


async def _load_projects(state: FSMContext) -> list[Project] | None:
    data = await state.get_data()
    raw = data.get(_PROJECTS_KEY)
    if not isinstance(raw, list):
        return None
    return [
        Project(id=int(r["id"]), name=str(r["name"]), path_with_namespace=str(r["path"]))
        for r in raw
    ]


def _find_project(projects: list[Project] | None, pid: int) -> Project | None:
    if not projects:
        return None
    for p in projects:
        if p.id == pid:
            return p
    return None


async def _cache_pipes(state: FSMContext, pipes: list[TagPipeline]) -> None:
    await state.update_data(
        {
            _PIPES_KEY: [
                {
                    "id": p.id,
                    "web_url": p.web_url,
                    "ref": p.ref,
                    "status": p.status,
                    "updated_at": p.updated_at,
                }
                for p in pipes
            ]
        }
    )


async def _load_pipes(state: FSMContext) -> list[TagPipeline]:
    data = await state.get_data()
    raw = data.get(_PIPES_KEY) or []
    if not isinstance(raw, list):
        return []
    return [
        TagPipeline(
            id=int(r["id"]),
            web_url=str(r["web_url"]),
            ref=str(r["ref"]),
            status=str(r["status"]),
            updated_at=str(r["updated_at"]),
        )
        for r in raw
        if isinstance(r, dict)
    ]


def _node_to_dict(n: PipelineNode) -> dict[str, object]:
    return {
        "project_id": n.project_id,
        "pipeline_id": n.pipeline_id,
        "label": n.label,
        "status": n.status,
        "web_url": n.web_url,
    }


def _tree_from_state(data: dict[str, object]) -> list[PipelineNode]:
    raw = data.get(_TREE_KEY) or []
    if not isinstance(raw, list):
        return []
    out: list[PipelineNode] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        out.append(
            PipelineNode(
                project_id=int(r["project_id"]),
                pipeline_id=int(r["pipeline_id"]),
                label=str(r["label"]),
                status=str(r.get("status", "")),
                web_url=str(r.get("web_url", "")),
            )
        )
    return out


def _ref_to_dict(p: JobRef) -> dict[str, object]:
    return {
        "project_id": p.project_id,
        "pipeline_id": p.pipeline_id,
        "job_id": p.job_id,
        "name": p.name,
        "stage": p.stage,
        "status": p.status,
        "node_label": p.node_label,
    }


def _refs_from_raw(raw: object) -> tuple[JobRef, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(
        JobRef(
            project_id=int(p["project_id"]),
            pipeline_id=int(p["pipeline_id"]),
            job_id=int(p["job_id"]),
            name=str(p["name"]),
            stage=str(p.get("stage", "")),
            status=str(p.get("status", "")),
            node_label=str(p.get("node_label", "")),
        )
        for p in raw
        if isinstance(p, dict)
    )


def _row_to_dict(r: JobRowStats) -> dict[str, object]:
    return {
        "name": r.name,
        "stage": r.stage,
        "stage_order": r.stage_order,
        "ready": r.ready,
        "running": r.running,
        "success": r.success,
        "failed": r.failed,
        "skipped": r.skipped,
        "total": r.total,
        "playable": [_ref_to_dict(p) for p in r.playable],
        "retryable": [_ref_to_dict(p) for p in r.retryable],
    }


def _rows_from_state(data: dict[str, object]) -> list[JobRowStats]:
    raw = data.get(_ROWS_KEY) or []
    if not isinstance(raw, list):
        return []
    out: list[JobRowStats] = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        out.append(
            JobRowStats(
                name=str(r["name"]),
                stage=str(r.get("stage", "")),
                stage_order=int(r.get("stage_order", 0)),
                ready=int(r.get("ready", 0)),
                running=int(r.get("running", 0)),
                success=int(r.get("success", 0)),
                failed=int(r.get("failed", 0)),
                skipped=int(r.get("skipped", 0)),
                total=int(r.get("total", 0)),
                playable=_refs_from_raw(r.get("playable")),
                retryable=_refs_from_raw(r.get("retryable")),
            )
        )
    return out
