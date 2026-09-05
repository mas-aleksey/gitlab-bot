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


@pytest.fixture
async def recent_pipeline_id(
    client: GitLabClient, auth: GitLabAuth, project_id: int
) -> int:
    pipelines = await client.list_recent_pipelines(auth, project_id=project_id, limit=1)
    assert pipelines, "project must have at least one pipeline for tests"
    return pipelines[0].id


@pytest.mark.vcr
async def test_list_recent_pipelines(
    client: GitLabClient, auth: GitLabAuth, project_id: int
) -> None:
    items = await client.list_recent_pipelines(auth, project_id=project_id, limit=5)
    assert items
    assert len(items) <= 5
    assert all(p.id > 0 for p in items)


@pytest.mark.vcr
async def test_list_playable_jobs(
    client: GitLabClient, auth: GitLabAuth,
    project_id: int, recent_pipeline_id: int,
) -> None:
    jobs = await client.list_playable_jobs(
        auth, project_id=project_id, pipeline_id=recent_pipeline_id
    )
    # An empty list is a valid outcome (no manual jobs / no bridges).
    for j in jobs:
        assert j.id > 0
        assert j.action in ("play", "retry")


@pytest.mark.vcr
async def test_list_downstream_jobs(
    client: GitLabClient, auth: GitLabAuth,
    project_id: int, recent_pipeline_id: int,
) -> None:
    jobs = await client.list_downstream_jobs(
        auth, project_id=project_id, pipeline_id=recent_pipeline_id, label="test-bucket",
    )
    for j in jobs:
        assert j.project_path == "test-bucket"
