import pytest

from clients.gitlab import GitLabAuth, GitLabClient


@pytest.fixture
async def project_id(
    client: GitLabClient, auth: GitLabAuth, test_project_path: str
) -> int:
    projects = await client.list_projects_in_scope(
        auth, scope_path=test_project_path, scope_kind="project"
    )
    return projects[0].id


@pytest.mark.vcr
async def test_list_recent_tags(
    client: GitLabClient, auth: GitLabAuth, project_id: int
) -> None:
    tags = await client.list_recent_tags(auth, project_id=project_id, limit=5)
    assert len(tags) <= 5
    for t in tags:
        assert t.name
