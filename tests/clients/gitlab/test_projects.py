import pytest

from clients.gitlab import GitLabAuth, GitLabClient


@pytest.fixture
async def project_id(
    client: GitLabClient, auth: GitLabAuth, scope_path: str
) -> int:
    kind = await client.resolve_scope_kind(auth, scope_path)
    projects = await client.list_projects_in_scope(
        auth, scope_path=scope_path, scope_kind=kind
    )
    assert projects, "scope_path must resolve to at least one project"
    return projects[0].id


@pytest.mark.vcr
async def test_list_projects_in_scope(
    client: GitLabClient, auth: GitLabAuth, scope_path: str
) -> None:
    kind = await client.resolve_scope_kind(auth, scope_path)
    projects = await client.list_projects_in_scope(
        auth, scope_path=scope_path, scope_kind=kind
    )
    assert projects
    assert all(p.id > 0 for p in projects)
    assert all(p.path_with_namespace for p in projects)


@pytest.mark.vcr
async def test_list_branches(
    client: GitLabClient, auth: GitLabAuth, project_id: int
) -> None:
    branches = await client.list_branches(auth, project_id=project_id)
    assert branches
    assert any(b.default for b in branches)
