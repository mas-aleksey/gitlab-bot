"""``/tags`` — просмотр и создание тегов в проектах дефолтного подключения.

Flow: project picker → tag menu (recent list + «➕ Создать тег») →
create: выбор ref (кнопка default branch + «✍️ Ввести ref») → ввод имени →
POST в GitLab.
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

from clients.gitlab import GitLabAuth, GitLabBadRequest, GitLabConflict, GitLabNotFound
from clients.gitlab.schemas import Project, ScopeKind
from db.models import Connection
from features.tags import texts as t
from features.tags.service import TagsService
from features.tags.states import TagState
from shared import texts as shared_texts
from shared.audit import log_action
from shared.edit import edit_or_answer
from shared.keyboards import add_main_menu
from shared.middlewares import ConnectionMiddleware

router = Router(name="tags")
router.message.middleware(ConnectionMiddleware())
router.callback_query.middleware(ConnectionMiddleware())


_PAGE_SIZE = 8
_RECENT_LIMIT = 3

_PROJECTS_KEY = "tags_projects"
_PROJ_KEY = "tags_proj_id"
_DEFAULT_BRANCH_KEY = "tags_default_branch"
_REF_KEY = "tags_ref"


class TagCB(CallbackData, prefix="tag"):
    # actions:
    #   projects_page — pagination
    #   pick_proj     — user picked a project → tag menu
    #   create        — start create-tag flow (ref picker)
    #   use_default   — use default branch as ref
    #   type_ref      — flip FSM to accept free-text ref
    action: str
    arg: int = 0


# ---------- /tags entrypoint ----------


@router.message(Command("tags"))
async def on_tags(
    message: Message,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    await state.clear()
    text, markup = await _tags_entry(
        state=state, connection=connection, auth=auth, tags=tags
    )
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


async def _tags_entry(
    *,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    tags: TagsService,
) -> tuple[str, InlineKeyboardMarkup | None]:
    projects = await tags.list_projects(
        auth=auth,
        scope_path=connection.scope_path,
        scope_kind=cast(ScopeKind, connection.scope_kind),
    )
    if not projects:
        kb = InlineKeyboardBuilder()
        add_main_menu(kb)
        return t.TAGS_PROJECTS_EMPTY, kb.as_markup()

    await _cache_projects(state, projects)

    if len(projects) == 1:
        await state.update_data({_PROJ_KEY: projects[0].id})
        return _tag_menu_view(projects[0])

    return (
        t.TAGS_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=0),
    )


@router.callback_query(F.data == shared_texts.NAV_TAGS_CB)
async def on_nav_tags(
    callback: CallbackQuery,
    state: FSMContext,
    connection: Connection,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    await state.clear()
    if not isinstance(callback.message, Message):
        await callback.answer()
        return
    text, markup = await _tags_entry(
        state=state, connection=connection, auth=auth, tags=tags
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(TagCB.filter(F.action == "projects_page"))
async def on_projects_page(
    callback: CallbackQuery,
    callback_data: TagCB,
    state: FSMContext,
    connection: Connection,
) -> None:
    projects = await _load_projects(state)
    if projects is None:
        await callback.answer()
        return
    await edit_or_answer(
        callback,
        t.TAGS_PROJECTS_HEADER.format(scope_path=connection.scope_path),
        _projects_keyboard(projects, page=callback_data.arg),
    )
    await callback.answer()


@router.callback_query(TagCB.filter(F.action == "pick_proj"))
async def on_pick_proj(
    callback: CallbackQuery,
    callback_data: TagCB,
    state: FSMContext,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    await state.update_data({_PROJ_KEY: project.id})
    text, markup = _tag_menu_view(project)
    await edit_or_answer(callback, text, markup)
    await callback.answer()


# ---------- create tag ----------


@router.callback_query(TagCB.filter(F.action == "create"))
async def on_create(
    callback: CallbackQuery,
    callback_data: TagCB,
    state: FSMContext,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return

    branches = await tags.list_branches(auth=auth, project_id=project.id)
    default = next((b for b in branches if b.default), None)
    default_name = default.name if default is not None else None
    await state.update_data(
        {_PROJ_KEY: project.id, _DEFAULT_BRANCH_KEY: default_name}
    )

    kb = InlineKeyboardBuilder()
    if default_name is not None:
        kb.button(
            text=f"⭐ {default_name}",
            callback_data=TagCB(action="use_default", arg=project.id).pack(),
        )
    kb.button(
        text=t.TAGS_BTN_TYPE_REF,
        callback_data=TagCB(action="type_ref", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        _project_header(project) + "\n\n" + t.TAGS_REF_ASK,
        kb.as_markup(),
    )
    await callback.answer()


@router.callback_query(TagCB.filter(F.action == "use_default"))
async def on_use_default(
    callback: CallbackQuery,
    callback_data: TagCB,
    state: FSMContext,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    data = await state.get_data()
    default_name = data.get(_DEFAULT_BRANCH_KEY)
    if not isinstance(default_name, str):
        await callback.answer(t.TAGS_REF_NOT_FOUND, show_alert=True)
        return
    await _prompt_tag_name(
        callback, state, project=project, ref=default_name, auth=auth, tags=tags
    )


@router.callback_query(TagCB.filter(F.action == "type_ref"))
async def on_type_ref(
    callback: CallbackQuery,
    callback_data: TagCB,
    state: FSMContext,
) -> None:
    projects = await _load_projects(state)
    project = _find_project(projects, callback_data.arg)
    if project is None:
        await callback.answer()
        return
    await state.update_data({_PROJ_KEY: project.id})
    await state.set_state(TagState.typing_ref)
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        _project_header(project) + "\n\n" + t.TAGS_REF_TYPE_IN,
        kb.as_markup(),
    )
    await callback.answer()


@router.message(TagState.typing_ref, F.text)
async def on_type_ref_input(
    message: Message,
    state: FSMContext,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    if message.text is None:
        return
    ref = message.text.strip()
    if not ref:
        await message.answer(t.TAGS_REF_TYPE_IN)
        return
    projects = await _load_projects(state)
    data = await state.get_data()
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None:
        await state.set_state(state=None)
        return
    await state.update_data({_REF_KEY: ref})
    await state.set_state(TagState.typing_name)
    recent_block = await _recent_tags_block(auth=auth, tags=tags, project=project)
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await message.answer(
        t.TAGS_NAME_ASK.format(ref=ref, recent=recent_block),
        reply_markup=kb.as_markup(),
    )


@router.message(TagState.typing_name, F.text)
async def on_type_name_input(
    message: Message,
    state: FSMContext,
    auth: GitLabAuth,
    tags: TagsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if message.text is None:
        return
    name = message.text.strip()
    if not name:
        await message.answer(t.TAGS_NAME_INVALID)
        return
    data = await state.get_data()
    ref = data.get(_REF_KEY)
    projects = await _load_projects(state)
    project = _find_project(projects, int(data.get(_PROJ_KEY, 0)))
    if project is None or not isinstance(ref, str):
        await state.set_state(state=None)
        return
    try:
        tag = await tags.create(
            auth=auth, project_id=project.id, tag_name=name, ref=ref
        )
    except GitLabConflict:
        await message.answer(t.TAGS_NAME_TAKEN)
        return
    except (GitLabBadRequest, GitLabNotFound) as e:
        logger.warning(
            "tag create refused: project={} ref={} name={} err={}",
            project.id, ref, name, e,
        )
        await message.answer(t.TAGS_REF_NOT_FOUND)
        return

    await state.set_state(state=None)
    logger.info(
        "tag created: project={} name={} ref={}",
        project.path_with_namespace, tag.name, ref,
    )
    if message.from_user is not None:
        await log_action(
            sessionmaker,
            actor_tg_id=message.from_user.id,
            action="tag.create",
            target=f"{project.path_with_namespace}@{tag.name}",
            base_url=auth.base_url,
            project_path=project.path_with_namespace,
        )
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await message.answer(
        t.TAGS_CREATED.format(
            name=tag.name, commit_sha=tag.commit_sha[:8], web_url=tag.web_url
        ),
        reply_markup=kb.as_markup(),
        disable_web_page_preview=False,
    )


async def _prompt_tag_name(
    callback: CallbackQuery,
    state: FSMContext,
    *,
    project: Project,
    ref: str,
    auth: GitLabAuth,
    tags: TagsService,
) -> None:
    await state.update_data({_REF_KEY: ref})
    await state.set_state(TagState.typing_name)
    recent_block = await _recent_tags_block(auth=auth, tags=tags, project=project)
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    await edit_or_answer(
        callback,
        _project_header(project)
        + "\n\n"
        + t.TAGS_NAME_ASK.format(ref=ref, recent=recent_block),
        kb.as_markup(),
    )
    await callback.answer()


async def _recent_tags_block(
    *, auth: GitLabAuth, tags: TagsService, project: Project
) -> str:
    recent = await tags.list_recent(
        auth=auth, project_id=project.id, limit=_RECENT_LIMIT
    )
    if not recent:
        return t.TAGS_NAME_RECENT_EMPTY
    return "\n".join(t.TAGS_NAME_RECENT_ROW.format(name=r.name) for r in recent)


# ---------- rendering ----------


def _project_header(project: Project) -> str:
    return f"<b>{project.name}</b>\n<code>{project.path_with_namespace}</code>"


def _tag_menu_view(project: Project) -> tuple[str, InlineKeyboardMarkup]:
    header = _project_header(project) + "\n\n" + t.TAGS_MENU_ASK

    kb = InlineKeyboardBuilder()
    kb.button(
        text=t.TAGS_BTN_CREATE,
        callback_data=TagCB(action="create", arg=project.id).pack(),
    )
    kb.adjust(1)
    add_main_menu(kb)
    return header, kb.as_markup()


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
            callback_data=TagCB(action="pick_proj", arg=p.id).pack(),
        )
    kb.adjust(1)

    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    text="‹ Назад",
                    callback_data=TagCB(action="projects_page", arg=page - 1).pack(),
                )
            )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    text="Вперёд ›",
                    callback_data=TagCB(action="projects_page", arg=page + 1).pack(),
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
