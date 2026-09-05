"""``/mrs`` — открытые MR в scope дефолтного подключения.

Flow:

1. ``/mrs`` → список открытых MR по scope подключения. Если MR из
   нескольких репо — сначала bucket-меню ``📦 shortname (count)``,
   иначе сразу плоский список.
2. Клик по MR → карточка: title-ссылка на GitLab, репо, автор, branches,
   статус (Ready / Draft / Merged / Closed), конфликты, approvers,
   description (trim 500).
3. В карточке для открытых MR: toggle self-approve, Merge, Approve as…
   Approve/unapprove от чужого имени пишутся в ``approval_events``
   (self-approve — нет, actor == owner).

Sha в CallbackData хранится префиксом (12 симв.) — Telegram callback data
ограничен 64 байтами. Полный sha сервер получит из preflight-запроса.
"""

from typing import cast

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.filters.callback_data import CallbackData
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from clients.gitlab import (
    GitLabAuth,
    GitLabBadRequest,
    GitLabConflict,
    GitLabForbidden,
    GitLabNotFound,
)
from clients.gitlab.schemas import MrDetail, MrListItem, ScopeKind
from db.models import Connection
from features.mrs import texts as t
from features.mrs.service import MrsService, ProxyApprovalService
from shared import texts as shared_texts
from shared.audit import log_action
from shared.edit import edit_or_answer
from shared.keyboards import add_main_menu
from shared.middlewares import ConnectionMiddleware

router = Router(name="mrs")
router.message.middleware(ConnectionMiddleware())
router.callback_query.middleware(ConnectionMiddleware())


class MrCB(CallbackData, prefix="mr"):
    # actions:
    #   list         — bucket-меню или плоский список
    #   list_proj    — плоский список по одному проекту (arg = project_id)
    #   detail       — карточка MR (arg = project_id, arg2 = mr_iid, sha)
    #   toggle       — self-approve toggle (arg = pid, arg2 = iid, sha)
    #   merge        — merge (arg = pid, arg2 = iid, sha)
    #   pick_proxy   — экран выбора proxy (arg = pid, arg2 = iid, sha)
    #   proxy_toggle — approve/unapprove через proxy (arg = pid, arg2 = iid,
    #                  sha, arg3 = proxy_conn_id)
    action: str
    arg: int = 0
    arg2: int = 0
    arg3: int = 0
    sha: str = ""


# ---------- entry ----------


@router.message(Command("mrs"))
async def on_mrs(
    message: Message,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
) -> None:
    text, markup = await _render_list(
        connection=connection, auth=auth, mrs=mrs, project_id=None
    )
    await message.answer(text, reply_markup=markup, disable_web_page_preview=True)


@router.callback_query(F.data == shared_texts.NAV_MRS_CB)
async def on_nav_mrs(
    callback: CallbackQuery,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
) -> None:
    text, markup = await _render_list(
        connection=connection, auth=auth, mrs=mrs, project_id=None
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(MrCB.filter(F.action == "list"))
async def on_list(
    callback: CallbackQuery,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
) -> None:
    text, markup = await _render_list(
        connection=connection, auth=auth, mrs=mrs, project_id=None
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(MrCB.filter(F.action == "list_proj"))
async def on_list_proj(
    callback: CallbackQuery,
    callback_data: MrCB,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
) -> None:
    text, markup = await _render_list(
        connection=connection, auth=auth, mrs=mrs, project_id=callback_data.arg
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


# ---------- detail ----------


@router.callback_query(MrCB.filter(F.action == "detail"))
async def on_detail(
    callback: CallbackQuery,
    callback_data: MrCB,
    auth: GitLabAuth,
    connection: Connection,
    mrs: MrsService,
) -> None:
    detail = await mrs.get_detail(
        auth=auth, project_id=callback_data.arg, mr_iid=callback_data.arg2
    )
    await _render_card(callback, detail=detail, connection=connection)
    await callback.answer()


# ---------- self-approve toggle ----------


@router.callback_query(MrCB.filter(F.action == "toggle"))
async def on_toggle(
    callback: CallbackQuery,
    callback_data: MrCB,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    try:
        approved = await mrs.toggle_self_approve(
            auth=auth,
            connection=connection,
            project_id=callback_data.arg,
            mr_iid=callback_data.arg2,
            sha=callback_data.sha,
        )
    except GitLabConflict:
        await callback.answer(t.MRS_TOAST_SHA_MISMATCH, show_alert=True)
        return
    except (GitLabForbidden, GitLabBadRequest):
        # 401 сюда не долетает — глобальный error-handler переведёт в ERR_AUTH.
        # 403/400: GitLab запрещает self-approve автора MR.
        await callback.answer(t.MRS_TOAST_SELF_APPROVAL, show_alert=True)
        return
    except GitLabNotFound:
        await callback.answer(t.MRS_TOAST_MR_CLOSED, show_alert=True)
        return

    await callback.answer(
        t.MRS_TOAST_APPROVED if approved else t.MRS_TOAST_UNAPPROVED
    )
    detail = await mrs.get_detail(
        auth=auth, project_id=callback_data.arg, mr_iid=callback_data.arg2
    )
    await log_action(
        sessionmaker,
        actor_tg_id=callback.from_user.id,
        action="mr.approve" if approved else "mr.unapprove",
        target=_mr_target(detail),
        base_url=connection.base_url,
        project_path=detail.project_path,
    )
    await _render_card(callback, detail=detail, connection=connection)


# ---------- merge ----------


@router.callback_query(MrCB.filter(F.action == "merge"))
async def on_merge(
    callback: CallbackQuery,
    callback_data: MrCB,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    try:
        await mrs.merge_mr(
            auth=auth,
            project_id=callback_data.arg,
            mr_iid=callback_data.arg2,
            sha=callback_data.sha,
        )
    except GitLabConflict:
        await callback.answer(t.MRS_TOAST_SHA_MISMATCH, show_alert=True)
        return
    except GitLabBadRequest as e:
        msg = str(e).lower()
        if "conflict" in msg:
            await callback.answer(t.MRS_TOAST_MERGE_CONFLICT, show_alert=True)
        else:
            await callback.answer(
                t.MRS_TOAST_MERGE_NOT_READY.format(reason=str(e)[:120]),
                show_alert=True,
            )
        return

    await callback.answer(t.MRS_TOAST_MERGED)
    detail = await mrs.get_detail(
        auth=auth, project_id=callback_data.arg, mr_iid=callback_data.arg2
    )
    await log_action(
        sessionmaker,
        actor_tg_id=callback.from_user.id,
        action="mr.merge",
        target=_mr_target(detail),
        base_url=connection.base_url,
        project_path=detail.project_path,
    )
    await _render_card(callback, detail=detail, connection=connection)


# ---------- proxy approve-as ----------


@router.callback_query(MrCB.filter(F.action == "pick_proxy"))
async def on_pick_proxy(
    callback: CallbackQuery,
    callback_data: MrCB,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
    proxy: ProxyApprovalService,
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    candidates = await proxy.list_candidates(
        caller_tg_id=callback.from_user.id, base_url=connection.base_url
    )
    if not candidates:
        await callback.answer(t.MRS_PROXY_EMPTY, show_alert=True)
        return

    # Живое состояние approvers — чтобы отметить, кто уже approved.
    detail = await mrs.get_detail(
        auth=auth, project_id=callback_data.arg, mr_iid=callback_data.arg2
    )
    text, markup = _proxy_menu(
        candidates=candidates,
        detail=detail,
        sha_prefix=callback_data.sha,
    )
    await edit_or_answer(callback, text, markup)
    await callback.answer()


@router.callback_query(MrCB.filter(F.action == "proxy_toggle"))
async def on_proxy_toggle(
    callback: CallbackQuery,
    callback_data: MrCB,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
    proxy: ProxyApprovalService,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> None:
    if callback.from_user is None:
        await callback.answer()
        return
    proxy_conn = await proxy.get_candidate(
        connection_id=callback_data.arg3,
        caller_tg_id=callback.from_user.id,
        base_url=connection.base_url,
    )
    if proxy_conn is None:
        await callback.answer(shared_texts.ERR_NOT_FOUND, show_alert=True)
        return
    try:
        result = await proxy.toggle(
            actor_tg_id=callback.from_user.id,
            proxy_conn=proxy_conn,
            project_id=callback_data.arg,
            mr_iid=callback_data.arg2,
            sha=callback_data.sha,
        )
    except GitLabConflict:
        await callback.answer(t.MRS_TOAST_SHA_MISMATCH, show_alert=True)
        return
    except (GitLabForbidden, GitLabBadRequest):
        await callback.answer(t.MRS_TOAST_SELF_APPROVAL, show_alert=True)
        return
    except GitLabNotFound:
        await callback.answer(t.MRS_TOAST_MR_CLOSED, show_alert=True)
        return

    await callback.answer(
        t.MRS_TOAST_APPROVED if result.approved else t.MRS_TOAST_UNAPPROVED
    )
    detail = await mrs.get_detail(
        auth=auth, project_id=callback_data.arg, mr_iid=callback_data.arg2
    )
    await log_action(
        sessionmaker,
        actor_tg_id=callback.from_user.id,
        action="mr.proxy_approve" if result.approved else "mr.proxy_unapprove",
        target=f"{_mr_target(detail)} как @{proxy_conn.gitlab_username}",
        base_url=connection.base_url,
        project_path=detail.project_path,
    )
    await _render_card(callback, detail=detail, connection=connection)


def _mr_target(detail: MrDetail) -> str:
    return f"{detail.project_name}!{detail.mr_iid}"


# ---------- rendering ----------


async def _render_list(
    *,
    connection: Connection,
    auth: GitLabAuth,
    mrs: MrsService,
    project_id: int | None,
) -> tuple[str, InlineKeyboardMarkup]:
    items = await mrs.list_in_scope(
        auth=auth,
        scope_path=connection.scope_path,
        scope_kind=cast(ScopeKind, connection.scope_kind),
        state="opened",
    )

    if not items:
        return t.MRS_LIST_EMPTY, _empty_keyboard()

    buckets: dict[int, tuple[str, list[MrListItem]]] = {}
    for it in items:
        path = _project_path_of(it) or str(it.project_id)
        existing = buckets.get(it.project_id)
        if existing is None:
            buckets[it.project_id] = (path, [it])
        else:
            existing[1].append(it)

    if project_id is not None and project_id in buckets:
        _, bucket_items = buckets[project_id]
        return _flat_list(connection.scope_path, bucket_items)

    if len(buckets) == 1:
        _, bucket_items = next(iter(buckets.values()))
        return _flat_list(connection.scope_path, bucket_items)

    return _bucket_menu(connection.scope_path, buckets, total=len(items))


def _flat_list(
    scope_path: str, items: list[MrListItem]
) -> tuple[str, InlineKeyboardMarkup]:
    header = t.MRS_LIST_HEADER.format(scope_path=scope_path, count=len(items))
    kb = InlineKeyboardBuilder()
    for item in items:
        kb.row(
            InlineKeyboardButton(
                text=_mr_row_label(item),
                callback_data=MrCB(
                    action="detail",
                    arg=item.project_id,
                    arg2=item.mr_iid,
                    sha=_sha_prefix(item.sha),
                ).pack(),
            )
        )
    add_main_menu(kb)
    return header, kb.as_markup()


def _bucket_menu(
    scope_path: str,
    buckets: dict[int, tuple[str, list[MrListItem]]],
    *,
    total: int,
) -> tuple[str, InlineKeyboardMarkup]:
    header = t.MRS_LIST_HEADER.format(scope_path=scope_path, count=total)
    ordered = sorted(buckets.items(), key=lambda kv: kv[1][0].lower())
    kb = InlineKeyboardBuilder()
    for pid, (path, bucket_items) in ordered:
        kb.row(
            InlineKeyboardButton(
                text=_project_button_label(path, len(bucket_items)),
                callback_data=MrCB(action="list_proj", arg=pid).pack(),
            )
        )
    add_main_menu(kb)
    return header, kb.as_markup()


def _empty_keyboard() -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    add_main_menu(kb)
    return kb.as_markup()


async def _render_card(
    callback: CallbackQuery,
    *,
    detail: MrDetail,
    connection: Connection,
) -> None:
    text = _card_text(detail)
    markup = _card_keyboard(detail=detail, connection=connection)
    await edit_or_answer(callback, text, markup)


def _card_text(d: MrDetail) -> str:
    if d.state == "merged":
        status = t.MRS_STATUS_MERGED
    elif d.state == "closed":
        status = t.MRS_STATUS_CLOSED
    elif d.draft:
        status = t.MRS_STATUS_DRAFT
    else:
        status = t.MRS_STATUS_READY

    conflict_line = (
        "\n" + t.MRS_FLAG_CONFLICTS if d.has_conflicts and d.state == "opened" else ""
    )
    approved_line = (
        t.MRS_APPROVALS_LINE.format(names=", ".join(_escape(n) for n in d.approver_names))
        if d.approver_names
        else t.MRS_APPROVALS_EMPTY
    )
    desc = (d.description or "").strip()
    if len(desc) > 500:
        desc = desc[:500].rstrip() + "…"
    desc_block = f"\n\n{_escape(desc)}" if desc else ""
    repo_line = (
        f"Репозиторий: <b>{_escape(d.project_name)}</b>\n" if d.project_name else ""
    )
    return (
        f'<b><a href="{d.web_url}">{_escape(d.title)}</a></b>\n'
        f"{repo_line}"
        f"Автор: {_escape(d.author_name)} (@{d.author_username})\n"
        f"<code>{_escape(d.source_branch)}</code> → "
        f"<code>{_escape(d.target_branch)}</code>\n"
        f"Статус: {status}"
        f"{conflict_line}\n"
        f"{approved_line}"
        f"{desc_block}"
    )


def _card_keyboard(
    *, detail: MrDetail, connection: Connection
) -> InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    sha_prefix = _sha_prefix(detail.sha)

    if detail.state == "opened":
        is_approved = connection.gitlab_user_id in detail.approver_ids
        kb.row(
            InlineKeyboardButton(
                text=t.MRS_BTN_APPROVE_ON if is_approved else t.MRS_BTN_APPROVE_OFF,
                callback_data=MrCB(
                    action="toggle",
                    arg=detail.project_id,
                    arg2=detail.mr_iid,
                    sha=sha_prefix,
                ).pack(),
            ),
            InlineKeyboardButton(
                text=t.MRS_BTN_MERGE,
                callback_data=MrCB(
                    action="merge",
                    arg=detail.project_id,
                    arg2=detail.mr_iid,
                    sha=sha_prefix,
                ).pack(),
            ),
        )
        kb.row(
            InlineKeyboardButton(
                text=t.MRS_BTN_APPROVE_AS,
                callback_data=MrCB(
                    action="pick_proxy",
                    arg=detail.project_id,
                    arg2=detail.mr_iid,
                    sha=sha_prefix,
                ).pack(),
            )
        )

    kb.row(
        InlineKeyboardButton(
            text=t.MRS_BTN_BACK_TO_LIST,
            callback_data=MrCB(action="list_proj", arg=detail.project_id).pack(),
        )
    )
    add_main_menu(kb)
    return kb.as_markup()


def _proxy_menu(
    *,
    candidates: list[Connection],
    detail: MrDetail,
    sha_prefix: str,
) -> tuple[str, InlineKeyboardMarkup]:
    kb = InlineKeyboardBuilder()
    for c in candidates:
        marker = (
            t.MRS_BTN_TOGGLE_ON
            if c.gitlab_user_id in detail.approver_ids
            else t.MRS_BTN_TOGGLE_OFF
        )
        kb.row(
            InlineKeyboardButton(
                text=f"{marker} {c.gitlab_username}",
                callback_data=MrCB(
                    action="proxy_toggle",
                    arg=detail.project_id,
                    arg2=detail.mr_iid,
                    arg3=c.id,
                    sha=sha_prefix,
                ).pack(),
            )
        )
    kb.row(
        InlineKeyboardButton(
            text=t.MRS_BTN_BACK_TO_LIST,
            callback_data=MrCB(
                action="detail",
                arg=detail.project_id,
                arg2=detail.mr_iid,
                sha=sha_prefix,
            ).pack(),
        )
    )
    add_main_menu(kb)
    return t.MRS_PROXY_ASK, kb.as_markup()


# ---------- helpers ----------


def _project_path_of(item: MrListItem) -> str:
    """Из ``references_full`` (``group/project!123``) — вернуть ``group/project``."""
    head, _, _ = (item.references_full or "").partition("!")
    return head


def _mr_row_label(item: MrListItem) -> str:
    """``[Author Name] — title`` с обрезанием до 64 символов."""
    caption = f"[{item.author_name}] — {item.title}"
    return caption if len(caption) <= 64 else caption[:61] + "..."


def _project_button_label(project_path: str, count: int) -> str:
    short = project_path.rsplit("/", 1)[-1] or project_path
    caption = f"📦 {short} ({count})"
    return caption if len(caption) <= 64 else caption[:61] + "..."


def _sha_prefix(sha: str) -> str:
    """12 симв. хватает для optimistic-lock и влезает в 64-байтный callback data."""
    return sha[:12]


def _escape(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
