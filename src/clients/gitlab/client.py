"""Async GitLab REST client.

One instance per application. ``base_url`` and ``pat`` are per-call
because a single bot serves multiple connections (different GitLab
hosts / users).

Errors: everything unhappy is raised as ``GitLabError`` or a subclass —
``GitLabAuthError`` (401), ``GitLabNotFound`` (404),
``GitLabForbidden`` (403), ``GitLabConflict`` (409),
``GitLabBadRequest`` (400). Higher layers translate to domain concepts.
"""

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx
from loguru import logger

from clients.base import ApiError, BaseClient, QueryParams
from clients.gitlab.schemas import (
    Branch,
    Job,
    JobAction,
    JobRef,
    MrDetail,
    MrListItem,
    MrState,
    PatOwner,
    Pipeline,
    PipelineNode,
    PlayedJob,
    Project,
    RecentPipeline,
    RecentTag,
    ScopeKind,
    TagInfo,
)

# ---------- exceptions ----------


class GitLabError(ApiError):
    """Any GitLab-side failure."""


class GitLabAuthError(GitLabError):
    """401 — invalid or revoked PAT."""


class GitLabForbidden(GitLabError):
    """403 — PAT lacks required scope / no access."""


class GitLabNotFound(GitLabError):
    """404 — resource does not exist or is invisible to this PAT."""


class GitLabConflict(GitLabError):
    """409 — resource already exists, sha mismatch, etc."""


class GitLabBadRequest(GitLabError):
    """400 — invalid payload, refused pipeline creation, etc."""


# ---------- auth context ----------


@dataclass(frozen=True, slots=True)
class GitLabAuth:
    """Per-request credentials & host."""

    base_url: str
    pat: str

    @property
    def api_base(self) -> str:
        return self.base_url.rstrip("/") + "/api/v4"

    @property
    def headers(self) -> dict[str, str]:
        return {"PRIVATE-TOKEN": self.pat}


# ---------- constants ----------


_DOWNSTREAM_MAX_DEPTH = 3
_PER_PAGE = 100


# ---------- client ----------


class GitLabClient(BaseClient):
    """Thin async facade over GitLab REST v4 API."""

    # ---------- low-level helpers ----------

    async def _get(
        self, auth: GitLabAuth, path: str, params: QueryParams = None
    ) -> httpx.Response:
        return await self._call(auth, "GET", path, params=params)

    async def _post(
        self, auth: GitLabAuth, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await self._call(auth, "POST", path, json=json)

    async def _put(
        self, auth: GitLabAuth, path: str, json: dict[str, Any] | None = None
    ) -> httpx.Response:
        return await self._call(auth, "PUT", path, json=json)

    async def _delete(self, auth: GitLabAuth, path: str) -> httpx.Response:
        return await self._call(auth, "DELETE", path)

    async def _call(
        self,
        auth: GitLabAuth,
        method: str,
        path: str,
        *,
        params: QueryParams = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        try:
            return await self._request(
                method,
                f"{auth.api_base}{path}",
                headers=auth.headers,
                params=params,
                json=json,
            )
        except httpx.HTTPStatusError as e:
            raise self._translate(e) from e

    @staticmethod
    def _translate(e: httpx.HTTPStatusError) -> GitLabError:
        code = e.response.status_code
        text = e.response.text
        msg = f"GitLab {code}: {text[:200]}"
        cls: type[GitLabError]
        if code == 401:
            cls = GitLabAuthError
        elif code == 403:
            cls = GitLabForbidden
        elif code == 404:
            cls = GitLabNotFound
        elif code == 409:
            cls = GitLabConflict
        elif code == 400:
            cls = GitLabBadRequest
        else:
            cls = GitLabError
        return cls(msg, status_code=code, response_text=text)

    async def _paginate(
        self, auth: GitLabAuth, path: str, params: QueryParams = None
    ) -> list[dict[str, Any]]:
        """Follow Link-header pagination, return combined items."""
        items: list[dict[str, Any]] = []
        page = 1
        base_params: list[tuple[str, Any]] = list(
            params.items() if isinstance(params, dict) else (params or [])
        )
        if not any(k == "per_page" for k, _ in base_params):
            base_params.append(("per_page", _PER_PAGE))
        while True:
            call_params = [*base_params, ("page", page)]
            r = await self._get(auth, path, params=call_params)
            batch = r.json()
            if not isinstance(batch, list) or not batch:
                break
            items.extend(batch)
            link = r.headers.get("link", "")
            if 'rel="next"' not in link:
                break
            page += 1
        return items

    # ---------- identity ----------

    async def resolve_pat_owner(self, auth: GitLabAuth) -> PatOwner:
        """Whoever this PAT belongs to. Raises ``GitLabAuthError`` on 401."""
        r = await self._get(auth, "/user")
        d = r.json()
        uid = d.get("id")
        uname = d.get("username")
        if uid is None or not uname:
            raise GitLabAuthError("GitLab /user returned no id/username", status_code=200)
        return PatOwner(user_id=int(uid), username=str(uname))

    async def resolve_scope_kind(self, auth: GitLabAuth, scope_path: str) -> ScopeKind:
        """Is ``scope_path`` a group or a project? Raises ``GitLabNotFound`` if neither."""
        try:
            await self._get(auth, f"/groups/{_encode(scope_path)}")
            return "group"
        except GitLabNotFound:
            pass
        await self._get(auth, f"/projects/{_encode(scope_path)}")
        return "project"

    # ---------- projects ----------

    async def list_projects_in_scope(
        self, auth: GitLabAuth, *, scope_path: str, scope_kind: ScopeKind
    ) -> list[Project]:
        if scope_kind == "group":
            data = await self._paginate(
                auth,
                f"/groups/{_encode(scope_path)}/projects",
                params={"include_subgroups": "true", "archived": "false"},
            )
        else:
            r = await self._get(auth, f"/projects/{_encode(scope_path)}")
            data = [r.json()]
        return [_project(p) for p in data]

    async def list_branches(self, auth: GitLabAuth, *, project_id: int) -> list[Branch]:
        data = await self._paginate(auth, f"/projects/{project_id}/repository/branches")
        return [
            Branch(name=str(b["name"]), default=bool(b.get("default", False)))
            for b in data
        ]

    # ---------- pipelines ----------

    async def create_pipeline(
        self, auth: GitLabAuth, *, project_id: int, ref: str
    ) -> Pipeline:
        try:
            r = await self._post(
                auth, f"/projects/{project_id}/pipeline", json={"ref": ref}
            )
        except GitLabBadRequest as e:
            # GitLab returns 400 with "Reference not found" if ref is missing.
            logger.warning("create_pipeline refused for {}@{}: {}", project_id, ref, e)
            raise
        d = r.json()
        return Pipeline(
            id=int(d["id"]),
            web_url=str(d["web_url"]),
            ref=str(d.get("ref", ref)),
            status=str(d.get("status", "created")),
        )

    async def list_recent_pipelines(
        self, auth: GitLabAuth, *, project_id: int, limit: int
    ) -> list[RecentPipeline]:
        r = await self._get(
            auth,
            f"/projects/{project_id}/pipelines",
            params={"order_by": "id", "sort": "desc", "per_page": limit, "page": 1},
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

    async def list_playable_jobs(
        self, auth: GitLabAuth, *, project_id: int, pipeline_id: int
    ) -> list[Job]:
        """Manual + retryable completed + running/pending (read-only) + bridges."""
        # Confirm pipeline exists — surfaces 404 as GitLabNotFound.
        await self._get(auth, f"/projects/{project_id}/pipelines/{pipeline_id}")

        items: list[Job] = []
        seen: set[int] = set()

        manual = await self._paginate(
            auth,
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            params={"scope[]": "manual"},
        )
        for j in manual:
            jid = int(j["id"])
            if jid in seen:
                continue
            seen.add(jid)
            items.append(_job(j, project_id, action="play"))

        completed = await self._paginate(
            auth,
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            params=[
                ("scope[]", "success"),
                ("scope[]", "failed"),
                ("scope[]", "canceled"),
                ("scope[]", "skipped"),
            ],
        )
        for j in completed:
            jid = int(j["id"])
            if jid in seen:
                continue
            seen.add(jid)
            items.append(_job(j, project_id, action="retry"))

        # Read-only: показываем бегущие джобы, но запускать их нельзя.
        in_flight = await self._paginate(
            auth,
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            params=[("scope[]", "running"), ("scope[]", "pending")],
        )
        for j in in_flight:
            jid = int(j["id"])
            if jid in seen:
                continue
            seen.add(jid)
            items.append(_job(j, project_id, action="play", playable=False))

        bridges = await self._paginate(
            auth, f"/projects/{project_id}/pipelines/{pipeline_id}/bridges"
        )
        for b in bridges:
            bid = int(b["id"])
            if bid in seen:
                continue
            downstream = b.get("downstream_pipeline") or {}
            ds_id = downstream.get("id")
            ds_project_id = downstream.get("project_id")
            status = str(b.get("status", ""))
            trigger_name = str(b.get("name", "")).removeprefix("trigger ")
            # Manual bridge (когда downstream ещё не создан) — играется через
            # POST /jobs/:id/play, как обычная manual-джоба. Если ds уже есть —
            # это drill-placeholder, играть не даём (пользователь идёт внутрь).
            if ds_id is not None and ds_project_id is not None:
                items.append(
                    Job(
                        id=bid,
                        name=trigger_name,
                        stage=str(b.get("stage", "")),
                        status=status,
                        project_id=project_id,
                        action="play",
                        project_path=trigger_name,
                        downstream_project_id=int(ds_project_id),
                        downstream_pipeline_id=int(ds_id),
                    )
                )
            elif status in {"manual", "created", "skipped"}:
                seen.add(bid)
                items.append(
                    Job(
                        id=bid,
                        name=trigger_name,
                        stage=str(b.get("stage", "")),
                        status=status,
                        project_id=project_id,
                        action="play",
                        project_path="",
                    )
                )
            elif status in {"failed", "canceled"}:
                seen.add(bid)
                items.append(
                    Job(
                        id=bid,
                        name=trigger_name,
                        stage=str(b.get("stage", "")),
                        status=status,
                        project_id=project_id,
                        action="retry",
                        project_path="",
                    )
                )
        return items

    async def list_downstream_jobs(
        self, auth: GitLabAuth, *, project_id: int, pipeline_id: int, label: str
    ) -> list[Job]:
        """Walk nested downstream pipelines (parent-child / multi-project)."""
        items: list[Job] = []
        seen_jobs: set[int] = set()
        visited: set[tuple[int, int]] = set()
        await self._walk_downstream(
            auth,
            project_id=project_id,
            pipeline_id=pipeline_id,
            label=label,
            items=items,
            seen_jobs=seen_jobs,
            visited=visited,
            depth=0,
        )
        return items

    async def _walk_downstream(
        self,
        auth: GitLabAuth,
        *,
        project_id: int,
        pipeline_id: int,
        label: str,
        items: list[Job],
        seen_jobs: set[int],
        visited: set[tuple[int, int]],
        depth: int,
    ) -> None:
        key = (project_id, pipeline_id)
        if key in visited:
            return
        visited.add(key)

        try:
            await self._get(auth, f"/projects/{project_id}/pipelines/{pipeline_id}")
        except GitLabNotFound:
            logger.warning(
                "downstream pipeline {}/{} not found — skipping", project_id, pipeline_id
            )
            return

        manual = await self._paginate(
            auth,
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            params={"scope[]": "manual"},
        )
        for j in manual:
            jid = int(j["id"])
            if jid in seen_jobs:
                continue
            seen_jobs.add(jid)
            items.append(_job(j, project_id, action="play", project_path=label))

        completed = await self._paginate(
            auth,
            f"/projects/{project_id}/pipelines/{pipeline_id}/jobs",
            params=[
                ("scope[]", "success"),
                ("scope[]", "failed"),
                ("scope[]", "canceled"),
                ("scope[]", "skipped"),
            ],
        )
        for j in completed:
            jid = int(j["id"])
            if jid in seen_jobs:
                continue
            seen_jobs.add(jid)
            items.append(_job(j, project_id, action="retry", project_path=label))

        if depth + 1 >= _DOWNSTREAM_MAX_DEPTH:
            return

        bridges = await self._paginate(
            auth, f"/projects/{project_id}/pipelines/{pipeline_id}/bridges"
        )
        for b in bridges:
            downstream = b.get("downstream_pipeline") or {}
            ds_id = downstream.get("id")
            ds_project_id = downstream.get("project_id")
            if ds_id is None or ds_project_id is None:
                continue
            await self._walk_downstream(
                auth,
                project_id=int(ds_project_id),
                pipeline_id=int(ds_id),
                label=label,
                items=items,
                seen_jobs=seen_jobs,
                visited=visited,
                depth=depth + 1,
            )

    async def play_job(
        self,
        auth: GitLabAuth,
        *,
        project_id: int,
        job_id: int,
        action: JobAction = "play",
    ) -> PlayedJob:
        endpoint = "retry" if action == "retry" else "play"
        r = await self._post(auth, f"/projects/{project_id}/jobs/{job_id}/{endpoint}")
        d = r.json()
        return PlayedJob(
            id=int(d.get("id", job_id)),
            name=str(d.get("name", "")),
            status=str(d.get("status", "")),
            web_url=str(d.get("web_url", "")),
        )

    # ---------- release-flow helpers ----------

    async def find_pipeline_by_ref(
        self, auth: GitLabAuth, *, project_id: int, ref: str
    ) -> Pipeline | None:
        """Свежайший pipeline проекта на заданном ``ref`` (ветка или тег).

        Возвращает None, если такого пайпа ещё нет — GitLab создаёт pipeline
        на push тега асинхронно, вызывающий должен ретраить.
        """
        r = await self._get(
            auth,
            f"/projects/{project_id}/pipelines",
            params={"ref": ref, "order_by": "id", "sort": "desc", "per_page": 1},
        )
        data = r.json()
        if not isinstance(data, list) or not data:
            return None
        p = data[0]
        return Pipeline(
            id=int(p["id"]),
            web_url=str(p.get("web_url", "")),
            ref=str(p.get("ref", ref)),
            status=str(p.get("status", "")),
        )

    async def walk_pipeline_tree(
        self, auth: GitLabAuth, *, project_id: int, pipeline_id: int
    ) -> list[PipelineNode]:
        """BFS-обход root + все downstream (child/multi-project) до max depth.

        Один узел = один pipeline. Обход идёт по слоям: внутри слоя все
        запросы ``/pipelines/:id`` + ``/bridges`` уходят параллельно через
        ``asyncio.gather``. ``path_with_namespace`` каждого проекта тянем
        один раз — на большом дереве узлы часто из одного проекта.
        """
        nodes: list[PipelineNode] = []
        visited: set[tuple[int, int]] = set()
        project_paths: dict[int, str] = {}
        layer: list[tuple[int, int]] = [(project_id, pipeline_id)]
        depth = 0
        while layer:
            fresh = [key for key in layer if key not in visited]
            visited.update(fresh)
            if not fresh:
                break

            missing_paths = {pid for pid, _ in fresh if pid not in project_paths}
            path_tasks = [
                self._get(auth, f"/projects/{pid}") for pid in missing_paths
            ]
            pipeline_tasks = [
                self._get(auth, f"/projects/{pid}/pipelines/{plid}")
                for pid, plid in fresh
            ]
            bridge_tasks: list[Any] = []
            if depth + 1 < _DOWNSTREAM_MAX_DEPTH:
                bridge_tasks = [
                    self._paginate(auth, f"/projects/{pid}/pipelines/{plid}/bridges")
                    for pid, plid in fresh
                ]
            path_results, pipeline_results, bridge_results = await asyncio.gather(
                asyncio.gather(*path_tasks, return_exceptions=True),
                asyncio.gather(*pipeline_tasks, return_exceptions=True),
                asyncio.gather(*bridge_tasks, return_exceptions=True),
            )
            for pid, res in zip(missing_paths, path_results):
                if isinstance(res, BaseException):
                    project_paths[pid] = str(pid)
                    continue
                project_paths[pid] = str(res.json().get("path_with_namespace", "")) or str(pid)

            next_layer: list[tuple[int, int]] = []
            for i, (pid, plid) in enumerate(fresh):
                pr = pipeline_results[i]
                if isinstance(pr, BaseException):
                    logger.warning("pipeline {}/{} not found — skipping: {}", pid, plid, pr)
                    continue
                d = pr.json()
                label = project_paths[pid].rsplit("/", 1)[-1]
                nodes.append(
                    PipelineNode(
                        project_id=pid,
                        pipeline_id=plid,
                        label=label,
                        status=str(d.get("status", "")),
                        web_url=str(d.get("web_url", "")),
                    )
                )
                if not bridge_tasks:
                    continue
                br = bridge_results[i]
                if isinstance(br, BaseException):
                    continue
                for b in br:
                    downstream = b.get("downstream_pipeline") or {}
                    ds_id = downstream.get("id")
                    ds_project_id = downstream.get("project_id")
                    if ds_id is None or ds_project_id is None:
                        continue
                    next_layer.append((int(ds_project_id), int(ds_id)))
            layer = next_layer
            depth += 1
        return nodes

    async def list_all_jobs(
        self, auth: GitLabAuth, *, tree: list[PipelineNode]
    ) -> list[JobRef]:
        """Все джобы во всех узлах дерева — один ``/jobs`` на узел, параллельно.

        Без ``scope``-фильтра: релиз-экран группирует по имени и считает
        статусы, включая ``created``/``skipped``, — фильтровать здесь нельзя.
        """
        if not tree:
            return []
        tasks = [
            self._paginate(
                auth,
                f"/projects/{node.project_id}/pipelines/{node.pipeline_id}/jobs",
            )
            for node in tree
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        refs: list[JobRef] = []
        for node, jobs in zip(tree, results):
            if isinstance(jobs, BaseException):
                logger.warning(
                    "jobs fetch failed for {}/{}: {}",
                    node.project_id,
                    node.pipeline_id,
                    jobs,
                )
                continue
            for j in jobs:
                refs.append(
                    JobRef(
                        project_id=node.project_id,
                        pipeline_id=node.pipeline_id,
                        job_id=int(j["id"]),
                        name=str(j.get("name", "")),
                        stage=str(j.get("stage", "")),
                        status=str(j.get("status", "")),
                        node_label=node.label,
                    )
                )
        return refs

    # ---------- merge requests ----------

    async def list_authored_open_mrs(
        self, auth: GitLabAuth, *, state: MrState = "opened"
    ) -> list[MrListItem]:
        data = await self._paginate(
            auth,
            "/merge_requests",
            params={"scope": "created_by_me", "state": state},
        )
        return [_mr_list_item(m) for m in data]

    async def list_open_mrs_in_scope(
        self,
        auth: GitLabAuth,
        *,
        scope_path: str,
        scope_kind: ScopeKind,
        state: MrState = "opened",
    ) -> list[MrListItem]:
        if scope_kind == "group":
            path = f"/groups/{_encode(scope_path)}/merge_requests"
        else:
            path = f"/projects/{_encode(scope_path)}/merge_requests"
        data = await self._paginate(auth, path, params={"state": state})
        return [_mr_list_item(m) for m in data]

    async def get_mr_detail(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int
    ) -> MrDetail:
        r = await self._get(auth, f"/projects/{project_id}/merge_requests/{mr_iid}")
        attrs = r.json()
        proj = await self._get(auth, f"/projects/{project_id}")
        project_name = str(proj.json().get("name") or "")
        project_path = str(proj.json().get("path_with_namespace") or "")

        approvers_raw = attrs.get("approved_by")
        if not approvers_raw:
            try:
                approvals = await self._get(
                    auth, f"/projects/{project_id}/merge_requests/{mr_iid}/approvals"
                )
                approvers_raw = approvals.json().get("approved_by") or []
            except GitLabError as e:
                logger.warning("approvals fetch failed for MR {}: {}", mr_iid, e)
                approvers_raw = []

        approver_ids: set[int] = set()
        approver_names: list[str] = []
        for entry in approvers_raw:
            user = entry.get("user") if isinstance(entry, dict) else None
            if not isinstance(user, dict):
                continue
            uid = user.get("id")
            if uid is not None:
                approver_ids.add(int(uid))
            approver_names.append(
                str(user.get("name") or user.get("username") or "unknown")
            )
        author = attrs.get("author") or {}
        return MrDetail(
            project_id=project_id,
            project_name=project_name,
            project_path=project_path,
            mr_iid=mr_iid,
            sha=str(attrs["sha"]),
            title=str(attrs["title"]),
            web_url=str(attrs["web_url"]),
            state=str(attrs.get("state") or "opened"),
            description=str(attrs.get("description") or ""),
            source_branch=str(attrs.get("source_branch") or ""),
            target_branch=str(attrs.get("target_branch") or ""),
            draft=bool(attrs.get("draft") or attrs.get("work_in_progress") or False),
            has_conflicts=bool(attrs.get("has_conflicts") or False),
            author_gitlab_id=int(author.get("id") or 0),
            author_username=str(author.get("username") or "unknown"),
            author_name=str(author.get("name") or author.get("username") or "unknown"),
            approver_ids=frozenset(approver_ids),
            approver_names=tuple(approver_names),
        )

    async def get_codeowner_approvers(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int
    ) -> frozenset[int]:
        r = await self._get(
            auth, f"/projects/{project_id}/merge_requests/{mr_iid}/approval_state"
        )
        payload = r.json()
        if not isinstance(payload, dict):
            return frozenset()
        approvers: set[int] = set()
        for rule in payload.get("rules") or []:
            if rule.get("rule_type") != "code_owner":
                continue
            for approver in rule.get("eligible_approvers") or []:
                approvers.add(int(approver["id"]))
        return frozenset(approvers)

    # ---------- approvals ----------

    async def approve(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int, sha: str
    ) -> None:
        """Approve MR with optimistic-lock on ``sha`` (partial prefix ok)."""
        # GitLab требует полный sha в /approve, а в callback data у нас только
        # 12-символьный префикс. Тянем актуальный HEAD и проверяем совпадение.
        actual_full = await self._resolve_full_sha(
            auth, project_id=project_id, mr_iid=mr_iid, sha_prefix=sha
        )
        await self._post(
            auth,
            f"/projects/{project_id}/merge_requests/{mr_iid}/approve",
            json={"sha": actual_full},
        )

    async def unapprove(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int
    ) -> None:
        await self._post(auth, f"/projects/{project_id}/merge_requests/{mr_iid}/unapprove")

    async def list_approvers(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int
    ) -> frozenset[int]:
        r = await self._get(
            auth, f"/projects/{project_id}/merge_requests/{mr_iid}/approvals"
        )
        payload = r.json()
        approved_by = payload.get("approved_by") or []
        return frozenset(int(e["user"]["id"]) for e in approved_by)

    # ---------- merge ----------

    async def merge(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int, sha: str
    ) -> None:
        """Merge MR with optimistic-lock on ``sha`` (partial prefix ok)."""
        actual_full = await self._resolve_full_sha(
            auth, project_id=project_id, mr_iid=mr_iid, sha_prefix=sha
        )
        await self._put(
            auth,
            f"/projects/{project_id}/merge_requests/{mr_iid}/merge",
            json={"sha": actual_full},
        )

    async def _resolve_full_sha(
        self, auth: GitLabAuth, *, project_id: int, mr_iid: int, sha_prefix: str
    ) -> str:
        """Fetch current HEAD, ensure it still starts with ``sha_prefix``.

        Callback data содержит только 12-символьный префикс — GitLab REST требует
        полный sha и в /merge, и в /approve. Если HEAD уехал — 409.
        """
        r = await self._get(auth, f"/projects/{project_id}/merge_requests/{mr_iid}")
        actual_full = str(r.json()["sha"])
        if not actual_full.lower().startswith(sha_prefix.lower()):
            raise GitLabConflict(
                f"sha mismatch: expected prefix {sha_prefix!r}, got {actual_full!r}",
                status_code=409,
            )
        return actual_full

    # ---------- releases (tags) ----------

    async def create_tag(
        self, auth: GitLabAuth, *, project_id: int, tag_name: str, ref: str
    ) -> TagInfo:
        r = await self._post(
            auth,
            f"/projects/{project_id}/repository/tags",
            json={"tag_name": tag_name, "ref": ref},
        )
        d = r.json()
        commit = d.get("commit") or {}
        commit_sha = str(commit.get("id") or commit.get("short_id") or "")
        # tag web url — construct from project path.
        proj = await self._get(auth, f"/projects/{project_id}")
        path = str(proj.json().get("path_with_namespace", ""))
        web_url = f"{auth.base_url.rstrip('/')}/{path}/-/tags/{tag_name}"
        return TagInfo(name=str(d.get("name", tag_name)), web_url=web_url, commit_sha=commit_sha)

    async def list_recent_tags(
        self, auth: GitLabAuth, *, project_id: int, limit: int
    ) -> list[RecentTag]:
        r = await self._get(
            auth,
            f"/projects/{project_id}/repository/tags",
            params={"order_by": "updated", "sort": "desc", "per_page": limit, "page": 1},
        )
        data = r.json()
        tags: list[RecentTag] = []
        for t in data[:limit]:
            commit = t.get("commit") or {}
            tags.append(
                RecentTag(
                    name=str(t.get("name", "")),
                    commit_sha=str(commit.get("short_id") or commit.get("id") or ""),
                )
            )
        return tags


# ---------- helpers ----------


def _encode(path: str) -> str:
    """URL-encode a group/project path for GitLab's ``:id`` parameter."""
    return path.replace("/", "%2F")


def _project(p: dict[str, Any]) -> Project:
    return Project(
        id=int(p["id"]),
        path_with_namespace=str(p["path_with_namespace"]),
        name=str(p.get("name") or p["path_with_namespace"]),
    )


def _job(
    j: dict[str, Any],
    project_id: int,
    *,
    action: JobAction,
    project_path: str = "",
    playable: bool = True,
) -> Job:
    return Job(
        id=int(j["id"]),
        name=str(j.get("name", "")),
        stage=str(j.get("stage", "")),
        status=str(j.get("status", "")),
        project_id=project_id,
        action=action,
        project_path=project_path,
        playable=playable,
    )


def _mr_list_item(m: dict[str, Any]) -> MrListItem:
    references = m.get("references") or {}
    author = m.get("author") or {}
    return MrListItem(
        project_id=int(m["project_id"]),
        mr_iid=int(m["iid"]),
        sha=str(m["sha"]),
        title=str(m["title"]),
        web_url=str(m["web_url"]),
        references_full=str(references.get("full") or f"!{m['iid']}"),
        author_gitlab_id=int(author.get("id") or 0),
        author_username=str(author.get("username") or "unknown"),
        author_name=str(author.get("name") or author.get("username") or "unknown"),
    )
