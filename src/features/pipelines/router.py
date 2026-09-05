"""``/pipelines`` — просмотр и запуск pipelines в дефолтном подключении.

Flow: project picker → mode picker → одно из двух:

* **Run on branch** — кнопка с дефолтной веткой + «✍️ Ввести имя ветки»
  (свободный ввод через FSM). Запускаем pipeline на выбранном ref.
* **Recent** — список последних pipelines → drill в pipeline → чек-бокс
  для playable джоб (manual/retry) → «▶️ Запустить выбранные».

Большие списки (проекты, recent, jobs) стешим в FSM и адресуем по
индексу — callback_data ограничен ~64 байт.
"""

from typing import Any, cast

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

from clients.gitlab import GitLabAuth, GitLabBadRequest, GitLabNotFound
from clients.gitlab.schemas import Job, JobAction, Project, RecentPipeline, ScopeKind
from db.models import Connection
from features.pipelines import texts as t
from features.pipelines.service import PipelinesService
from features.pipelines.states import PipelineState
from shared import texts as shared_texts
from shared.audit import log_action
from shared.edit import edit_or_answer
from shared.keyboards import add_main_menu
from shared.middlewares import ConnectionMiddleware

router = Router(name="pipelines")
router.message.middleware(ConnectionMiddleware())
router.callback_query.middleware(ConnectionMiddleware())


_PAGE_SIZE = 8
_RECENT_LIMIT = 10

_PROJECTS_KEY = "pipe_projects"
_PROJ_KEY = "pipe_proj_id"
_DEFAULT_BRANCH_KEY = "pipe_default_branch"
_RECENT_KEY = "pipe_recent"
_PIPELINE_ID_KEY = "pipe_pipeline_id"
_PIPELINE_URL_KEY = "pipe_pipeline_url"
_JOBS_KEY = "pipe_jobs"
_SELECTED_KEY = "pipe_selected"
# Текущий downstream bucket: None = корень pipeline; иначе dict
# {"project_id": int, "pipeline_id": int, "label": str}.
_BUCKET_KEY = "pipe_bucket"


class PipeCB(CallbackData, prefix="pipe"):
    # actions:
    #   projects_page — pagination of project picker
    #   pick_proj     — user picked a project → mode picker
    #   mode_branch   — go into "run on branch" flow
    #   mode_recent   — go into "recent pipelines" flow
    #   run_default   — create pipeline on default branch
    #   type_branch   — flip FSM to accept a free-text branch name
    #   recent_refresh — reload recent-pipelines list
    #   pick_pipe     — drill into a recent pipeline
    #   toggle_job    — flip selection of a job by index
    #   refresh_jobs  — reload jobs of current pipeline
    #   play_jobs     — start selected jobs
    #   back_to_recent — back to recent list from job picker
    #   pick_bucket   — drill into a downstream pipeline (bridge index)
    #   back_to_root  — back to root pipeline from a bucket
    #   noop          — filler (stage headers etc.)
    action: str
    # `arg`  — project_id, page number, index (context-dependent).
    # `arg2` — secondary id (pipeline_id where needed).
    arg: int = 0
    arg2: int = 0


# ---------- /pipelines entrypoint ----------


@router.message(Command("pipelines"))
async def on_pipelines(
    message: Message,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    await state.clear()
    text, markup = await _pipelines_entry(
        state=state,
        connection=connection,
        auth=auth,
        pipelines=pipelines,
    )
    await message.answer(text, reply_markup=markup)


@router.callback_query(F.data == shared_texts.NAV_PIPELINES_CB)
async def on_nav_pipelines(
    callback: CallbackQuery,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = await _pipelines_entry(
        state=state,
        connection=connection,
        auth=auth,
        pipelines=pipelines,
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


async def _pipelines_entry(
    *,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> tuple[str, InlineKeyboardMarkup | None]:
    """Entry-экран /pipelines: projects list или сразу mode picker (если проект один)."""
    projects = await pipelines.list_projects(
        auth=auth,
        scope_path=connection.scope_path,
        scope_kind=cast(ScopeKind, connection.scope_kind),
    )
    if not projects:
        kb = InlineKeyboardBuilder()
        add_main_menu(kb)
        return t.PIPE_PROJECTS_EMPTY, kb.as_markup()

    await _cache_projects(state, projects)

    if len(projects) == 1:
        await state.update_data({_PROJ_KEY: projects[0].id})
        return _mode_view(projects[0])

    return (
        t.PIPE_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=0),
    )


@router.callback_query(PipeCB.filter(F.action == "projects_page"))
async def on_projects_page(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    connection: Connection,
) -> None:
    projects = await _load_projects(state)
    if projects is None:
        await callback.answer()
        return
    await edit_or_answer(
        callback,
        t.PIPE_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=callback_data.arg),
    )
    await callback.answer()


@router.callback_query(PipeCB.filter(F.action == "pick_proj"))
async def on_pick_proj(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    await state.update_data({_PROJ_KEY: project.id})
    text, markup = _mode_view(project)
    await edit_or_answer(callback, text, markup)
    await callback.answer()


# ---------- mode: run on branch ----------


@router.callback_query(PipeCB.filter(F.action == "mode_branch"))
async def on_mode_branch(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return

    branches = await pipelines.list_branches(auth=auth, project_id=project.id)
    default = next((b for b in branches if b.default), None)
    default_name = default.name if default is not None else None
    await state.update_data(
        {_PROJ_KEY: project.id, _DEFAULT_BRANCH_KEY: default_name}
    )

    kb = InlineKeyboardBuilder()
    if default_name is not None:
        kb.button(
            text=f"⭐ {default_name}",
            callback_data=PipeCB(action="run_default", arg=project.id).pack(),
        )
    kb.button(
        text=t.PIPE_BTN_TYPE_BRANCH,
        callback_data=PipeCB(action="type_branch", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        _project_header(project) + "\n\n" + t.PIPE_BRANCH_ASK,
        kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(PipeCB.filter(F.action == "run_default"))
async def on_run_default(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    data = await state.get_data()
    default_name = data.get(_DEFAULT_BRANCH_KEY)
    if not isinstance(default_name, str):
        await callback.answer(t.PIPE_BRANCH_NOT_FOUND, show_alert=True)
        return
    await _run_pipeline(
        callback=callback,
        auth=auth,
        pipelines=pipelines,
        sessionmaker=sessionmaker,
        project=project,
        ref=default_name,
    )


@router.callback_query(PipeCB.filter(F.action == "type_branch"))
async def on_type_branch(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    await state.update_data({_PROJ_KEY: project.id})
    await state.set_state(PipelineState.typing_branch)
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await edit_or_answer(callback, t.PIPE_BRANCH_TYPE_IN, kb.as_markup())
    await callback.answer()


@router.message(PipelineState.typing_branch, F.text)
async def on_type_branch_input(
    message: Message,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if message.text is None:
        return
    ref = message.text.strip()
    if not ref:
        await message.answer(t.PIPE_BRANCH_TYPE_IN)
        return
    projects = await _load_projects(state)
    data = await state.get_data()
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None:
        await state.set_state(state=None)
        await message.answer(t.PIPE_BRANCH_NOT_FOUND)
        return
    await state.set_state(state=None)
    try:
        pipeline = await pipelines.create(auth=auth, project_id=project.id, ref=ref)
    except (GitLabBadRequest, GitLabNotFound) as e:
        logger.warning("pipeline refused: project={} ref={} err={}", project.id, ref, e)
        await message.answer(t.PIPE_BRANCH_NOT_FOUND)
        return
    logger.info(
        "pipeline created: project={} ref={} id={}",
        project.path_with_namespace, ref, pipeline.id,
    )
    if message.from_user is not None:
        await log_action(
            sessionmaker,
            actor_tg_id=message.from_user.id,
            action="pipeline.run",
            target=f"{project.path_with_namespace}@{ref} #{pipeline.id}",
            base_url=auth.base_url,
            project_path=project.path_with_namespace,
        )
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await message.answer(
        t.PIPE_RUN_OK.format(
            id=pipeline.id, ref=pipeline.ref, web_url=pipeline.web_url
        ),
        reply_markup=kb.as_markup(),
        disable_web_page_preview=False,
    )


# ---------- mode: recent pipelines ----------


@router.callback_query(PipeCB.filter(F.action.in_({"mode_recent", "recent_refresh"})))
async def on_mode_recent(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    recent = await pipelines.list_recent(
        auth=auth, project_id=project.id, limit=_RECENT_LIMIT
    )
    await state.update_data(
        {
            _PROJ_KEY: project.id,
            _RECENT_KEY: [
                {"id": r.id, "web_url": r.web_url, "ref": r.ref, "status": r.status,
                 "updated_at": r.updated_at}
                for r in recent
            ],
        }
    )
    await edit_or_answer(callback, _recent_view(project, recent), _recent_keyboard(project, recent))
    await callback.answer()


@router.callback_query(PipeCB.filter(F.action == "pick_pipe"))
async def on_pick_pipe(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    recent_raw = data.get(_RECENT_KEY)
    if project is None or not isinstance(recent_raw, list):
        await callback.answer()
        return
    idx = callback_data.arg
    if not 0 <= idx < len(recent_raw):
        await callback.answer()
        return
    picked = recent_raw[idx]
    pipeline_id = int(picked["id"])
    pipeline_url = str(picked.get("web_url", ""))
    await state.update_data(
        {
            _PIPELINE_ID_KEY: pipeline_id,
            _PIPELINE_URL_KEY: pipeline_url,
            _SELECTED_KEY: [],
            _BUCKET_KEY: None,
        }
    )
    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=True,
    )


@router.callback_query(PipeCB.filter(F.action == "toggle_job"))
async def on_toggle_job(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    jobs_raw = data.get(_JOBS_KEY)
    if project is None or not isinstance(jobs_raw, list):
        await callback.answer()
        return
    if not 0 <= callback_data.arg < len(jobs_raw):
        await callback.answer()
        return
    target = jobs_raw[callback_data.arg]
    if isinstance(target, dict) and not target.get("playable", True):
        await callback.answer()
        return
    selected_raw = data.get(_SELECTED_KEY) or []
    selected: set[int] = {int(i) for i in selected_raw if isinstance(i, int)}
    if callback_data.arg in selected:
        selected.remove(callback_data.arg)
    else:
        selected.add(callback_data.arg)
    await state.update_data({_SELECTED_KEY: sorted(selected)})
    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=False,
    )


@router.callback_query(PipeCB.filter(F.action == "refresh_jobs"))
async def on_refresh_jobs(
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None:
        await callback.answer()
        return
    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=True,
    )


@router.callback_query(PipeCB.filter(F.action == "pick_bucket"))
async def on_pick_bucket(
    callback: CallbackQuery,
    callback_data: PipeCB,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    """Drill в downstream pipeline: bridge index → сохраняем bucket + перерисовываем."""
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    jobs_raw = data.get(_JOBS_KEY)
    if project is None or not isinstance(jobs_raw, list):
        await callback.answer()
        return
    idx = callback_data.arg
    if not 0 <= idx < len(jobs_raw):
        await callback.answer()
        return
    bridge = jobs_raw[idx]
    if not isinstance(bridge, dict):
        await callback.answer()
        return
    dpid = bridge.get("downstream_project_id")
    dpipeid = bridge.get("downstream_pipeline_id")
    if not isinstance(dpid, int) or not isinstance(dpipeid, int):
        await callback.answer()
        return
    label = _bridge_label(bridge)
    await state.update_data(
        {
            _BUCKET_KEY: {"project_id": dpid, "pipeline_id": dpipeid, "label": label},
            _SELECTED_KEY: [],
        }
    )
    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=True,
    )


@router.callback_query(PipeCB.filter(F.action == "back_to_root"))
async def on_back_to_root(
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None:
        await callback.answer()
        return
    await state.update_data({_BUCKET_KEY: None, _SELECTED_KEY: []})
    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=True,
    )


@router.callback_query(PipeCB.filter(F.action == "back_to_recent"))
async def on_back_to_recent(
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None:
        await callback.answer()
        return
    recent = await pipelines.list_recent(
        auth=auth, project_id=project.id, limit=_RECENT_LIMIT
    )
    await state.update_data(
        {
            _RECENT_KEY: [
                {"id": r.id, "web_url": r.web_url, "ref": r.ref, "status": r.status,
                 "updated_at": r.updated_at}
                for r in recent
            ],
        }
    )
    await edit_or_answer(callback, _recent_view(project, recent), _recent_keyboard(project, recent))
    await callback.answer()


@router.callback_query(PipeCB.filter(F.action == "play_jobs"))
async def on_play_jobs(
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    jobs_raw = data.get(_JOBS_KEY)
    root_pipeline_id = data.get(_PIPELINE_ID_KEY)
    selected_raw = data.get(_SELECTED_KEY) or []
    if project is None or not isinstance(jobs_raw, list) or not isinstance(root_pipeline_id, int):
        await callback.answer()
        return
    selected = [int(i) for i in selected_raw if isinstance(i, int)]
    if not selected:
        await callback.answer(t.PIPE_JOBS_NOTHING_SELECTED, show_alert=True)
        return

    refs: list[tuple[int, int, JobAction]] = []
    for i in selected:
        if not 0 <= i < len(jobs_raw):
            continue
        j = jobs_raw[i]
        if not isinstance(j, dict) or "id" not in j:
            continue
        # bridge не запускается — по нему drill'ятся, а не играют
        if j.get("downstream_pipeline_id") is not None:
            continue
        if not j.get("playable", True):
            continue
        pid = int(j.get("project_id") or project.id)
        job_id = int(j["id"])
        action_raw = str(j.get("action", "play"))
        action: JobAction = "retry" if action_raw == "retry" else "play"
        refs.append((pid, job_id, action))

    await pipelines.play_jobs(auth=auth, refs=refs)
    if refs:
        await log_action(
            sessionmaker,
            actor_tg_id=callback.from_user.id,
            action="job.play",
            target=f"{project.path_with_namespace} #{root_pipeline_id} ×{len(refs)}",
            base_url=auth.base_url,
            project_path=project.path_with_namespace,
        )
    await state.update_data({_SELECTED_KEY: []})

    await _render_jobs(
        callback=callback,
        state=state,
        auth=auth,
        pipelines=pipelines,
        project=project,
        reload_jobs=True,
    )


@router.callback_query(PipeCB.filter(F.action == "noop"))
async def on_noop(callback: CallbackQuery) -> None:
    await callback.answer()


# ---------- rendering ----------


def _project_header(project: Project) -> str:
    return t.PIPE_PROJECT_HEADER.format(
        project_name=project.name,
        path_with_namespace=project.path_with_namespace,
    )


def _mode_view(project: Project) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    kb.button(
        text=t.PIPE_BTN_RUN_MODE,
        callback_data=PipeCB(action="mode_branch", arg=project.id).pack(),
    )
    kb.button(
        text=t.PIPE_BTN_RECENT_MODE,
        callback_data=PipeCB(action="mode_recent", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    return _project_header(project) + "\n\n" + t.PIPE_MODE_ASK, kb.as_markup()


def _recent_view(project: Project, recent: list[RecentPipeline]) -> str:
    header = _project_header(project) + "\n\n"
    if not recent:
        return header + t.PIPE_RECENT_EMPTY
    return header + t.PIPE_RECENT_ASK


def _recent_keyboard(
    project: Project, recent: list[RecentPipeline]
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    for idx, r in enumerate(recent):
        label = f"{_status_icon(r.status)} #{r.id} {r.ref}"
        if len(label) > 64:
            label = label[:61] + "..."
        kb.button(text=label, callback_data=PipeCB(action="pick_pipe", arg=idx).pack())
    kb.button(
        text=t.PIPE_BTN_REFRESH,
        callback_data=PipeCB(action="recent_refresh", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    return kb.as_markup()


async def _render_jobs(
    *,
    callback: CallbackQuery,
    state: FSMContext,
    auth: GitLabAuth,
    pipelines: PipelinesService,
    project: Project,
    reload_jobs: bool,
    answer_text: str | None = None,
    answer_alert: bool = False,
) -> None:
    """Рендер одного уровня pipeline: root или конкретный downstream bucket.

    Загружаем через ``list_playable_jobs`` для (project_id, pipeline_id)
    текущего уровня. Downstream'ы отображаются как одна кнопка на bridge
    (``📦 <label>``) — drill'аются кликом (``pick_bucket``). Не разворачиваем
    дерево целиком — иначе клавиатура превысит лимит Telegram.
    """
    data = await state.get_data()
    bucket = data.get(_BUCKET_KEY)
    root_pipeline_id = data.get(_PIPELINE_ID_KEY)
    pipeline_url = str(data.get(_PIPELINE_URL_KEY) or "")
    if not isinstance(root_pipeline_id, int):
        await callback.answer()
        return

    if isinstance(bucket, dict):
        level_project_id = int(bucket["project_id"])
        level_pipeline_id = int(bucket["pipeline_id"])
        level_label = str(bucket.get("label") or f"#{level_pipeline_id}")
    else:
        level_project_id = project.id
        level_pipeline_id = root_pipeline_id
        level_label = ""

    if reload_jobs:
        try:
            jobs = await pipelines.list_playable_jobs(
                auth=auth, project_id=level_project_id, pipeline_id=level_pipeline_id
            )
        except GitLabNotFound:
            await callback.answer(shared_texts.ERR_NOT_FOUND, show_alert=True)
            return
        jobs_raw = [_job_to_dict(j) for j in jobs]
        await state.update_data({_JOBS_KEY: jobs_raw})
    else:
        jobs_raw_any = data.get(_JOBS_KEY) or []
        jobs_raw = [j for j in jobs_raw_any if isinstance(j, dict)]

    fresh = await state.get_data()
    selected_raw = fresh.get(_SELECTED_KEY) or []
    selected: set[int] = {int(i) for i in selected_raw if isinstance(i, int)}

    header = f"Pipeline <b>#{root_pipeline_id}</b>"
    if pipeline_url:
        header += f' — <a href="{pipeline_url}">открыть</a>'
    if isinstance(bucket, dict):
        header += "\n" + t.PIPE_BUCKET_HEADER.format(label=level_label)

    back_button = _jobs_back_button(project.id, in_bucket=isinstance(bucket, dict))

    if not jobs_raw:
        kb = InlineKeyboardBuilder()
        kb.row(back_button)
        add_main_menu(kb)
        await edit_or_answer(callback, f"{header}\n\n{t.PIPE_JOBS_NONE}", kb.as_markup())
        if answer_text is not None:
            await callback.answer(answer_text, show_alert=answer_alert)
        else:
            await callback.answer()
        return

    regular_indices = sorted(
        (i for i, j in enumerate(jobs_raw) if j.get("downstream_pipeline_id") is None),
        key=lambda i: (_job_sort_label(jobs_raw[i]), str(jobs_raw[i].get("name", ""))),
    )
    bridge_indices = sorted(
        (i for i, j in enumerate(jobs_raw) if j.get("downstream_pipeline_id") is not None),
        key=lambda i: _bridge_label(jobs_raw[i]).lower(),
    )

    kb = InlineKeyboardBuilder()
    has_playable = False
    for idx in regular_indices:
        j = jobs_raw[idx]
        is_playable = bool(j.get("playable", True))
        if is_playable:
            has_playable = True
            kb.button(
                text=_job_label(j, selected=idx in selected, playable=True),
                callback_data=PipeCB(action="toggle_job", arg=idx).pack(),
            )
        else:
            kb.button(
                text=_job_label(j, selected=False, playable=False),
                callback_data=PipeCB(action="noop").pack(),
            )

    for idx in bridge_indices:
        j = jobs_raw[idx]
        kb.button(
            text=t.PIPE_BTN_BUCKET.format(label=_bridge_label(j)),
            callback_data=PipeCB(action="pick_bucket", arg=idx).pack(),
        )

    if has_playable:
        kb.button(
            text=t.PIPE_BTN_PLAY_SELECTED,
            callback_data=PipeCB(action="play_jobs").pack(),
        )
    kb.button(
        text=t.PIPE_BTN_REFRESH_JOBS,
        callback_data=PipeCB(action="refresh_jobs").pack(),
    )
    kb.adjust(1)
    kb.row(back_button)
    add_main_menu(kb)

    prompt = t.PIPE_JOBS_ASK if has_playable else t.PIPE_JOBS_NONE
    await edit_or_answer(callback, f"{header}\n\n{prompt}", kb.as_markup())
    if answer_text is not None:
        await callback.answer(answer_text, show_alert=answer_alert)
    else:
        await callback.answer()


def _jobs_back_button(project_id: int, *, in_bucket: bool) -> InlineKeyboardButton:
    if in_bucket:
        return InlineKeyboardButton(
            text=t.PIPE_BTN_BACK_TO_ROOT,
            callback_data=PipeCB(action="back_to_root").pack(),
        )
    return InlineKeyboardButton(
        text=t.PIPE_BTN_BACK_TO_RECENT,
        callback_data=PipeCB(action="back_to_recent", arg=project_id).pack(),
    )


def _bridge_label(bridge: dict[str, Any]) -> str:
    """Название downstream'а: project_path (короткий) или имя bridge-джобы."""
    project_path = str(bridge.get("project_path") or "")
    if project_path:
        return project_path.rsplit("/", 1)[-1]
    return str(bridge.get("name") or f"#{bridge.get('downstream_pipeline_id')}")


def _job_sort_label(j: dict[str, Any]) -> str:
    """Short label как в кнопке (last segment of project_path) для сортировки."""
    project_path = str(j.get("project_path") or "")
    if project_path:
        return project_path.rsplit("/", 1)[-1].lower()
    return ""


def _job_to_dict(j: Job) -> dict[str, Any]:
    return {
        "id": j.id,
        "name": j.name,
        "stage": j.stage,
        "status": j.status,
        "project_id": j.project_id,
        "action": j.action,
        "project_path": j.project_path,
        "downstream_project_id": j.downstream_project_id,
        "downstream_pipeline_id": j.downstream_pipeline_id,
        "playable": j.playable,
    }


def _job_label(j: dict[str, Any], *, selected: bool, playable: bool = True) -> str:
    project_path = str(j.get("project_path", ""))
    prefix = ""
    if project_path:
        short = project_path.rsplit("/", 1)[-1]
        prefix = f"[{short}] "
    status_icon = _status_icon(str(j.get("status", "")))
    if not playable:
        caption = f"{status_icon} {prefix}{j.get('name', '')}"
    else:
        mark = t.PIPE_JOB_SELECTED if selected else t.PIPE_JOB_UNSELECTED
        action_mark = "↻" if j.get("action") == "retry" else "▶"
        caption = f"{mark} {action_mark} {prefix}{j.get('name', '')} {status_icon}"
    return caption if len(caption) <= 64 else caption[:61] + "..."


def _status_icon(status: str) -> str:
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


def _projects_keyboard(projects: list[Project], *, page: int) -> InlineKeyboardMarkup:
    total_pages = max(1, (len(projects) + _PAGE_SIZE - 1) // _PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    start = page * _PAGE_SIZE
    slice_ = projects[start : start + _PAGE_SIZE]

    kb = InlineKeyboardBuilder()
    for p in slice_:
        kb.button(
            text=p.name,
            callback_data=PipeCB(action="pick_proj", arg=p.id).pack(),
        )
    kb.adjust(1)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="‹ Назад",
                    callback_data=PipeCB(action="projects_page", arg=page - 1).pack(),
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="Вперёд ›",
                    callback_data=PipeCB(action="projects_page", arg=page + 1).pack(),
                )
            )
        if nav:
            kb.row(*nav)

    add_main_menu(kb)
    return kb.as_markup()


# ---------- run helpers ----------


async def _run_pipeline(
    *,
    callback: CallbackQuery,
    auth: GitLabAuth,
    pipelines: PipelinesService,
    sessionmaker: async_sessionmaker[AsyncSession],
    project: Project,
    ref: str,
) -> None:
    try:
        pipeline = await pipelines.create(auth=auth, project_id=project.id, ref=ref)
    except (GitLabBadRequest, GitLabNotFound) as e:
        logger.warning("pipeline refused: project={} ref={} err={}", project.id, ref, e)
        await callback.answer(t.PIPE_BRANCH_NOT_FOUND, show_alert=True)
        return
    logger.info(
        "pipeline created: project={} ref={} id={}",
        project.path_with_namespace, ref, pipeline.id,
    )
    await log_action(
        sessionmaker,
        actor_tg_id=callback.from_user.id,
        action="pipeline.run",
        target=f"{project.path_with_namespace}@{ref} #{pipeline.id}",
        base_url=auth.base_url,
        project_path=project.path_with_namespace,
    )
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        t.PIPE_RUN_OK.format(
            id=pipeline.id, ref=pipeline.ref, web_url=pipeline.web_url
        ),
        kb.as_markup(),
        disable_web_page_preview=False,
    )
    await callback.answer()


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
