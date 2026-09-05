import pytest

from clients.gitlab import GitLabAuth, GitLabClient, GitLabNotFound


@pytest.mark.vcr
async def test_resolve_pat_owner(client: GitLabClient, auth: GitLabAuth) -> None:
    owner = await client.resolve_pat_owner(auth)
    assert owner.user_id > 0
    assert owner.username


@pytest.mark.vcr
async def test_resolve_scope_kind_project(
    client: GitLabClient, auth: GitLabAuth, scope_path: str
) -> None:
    kind = await client.resolve_scope_kind(auth, scope_path)
    assert kind in ("group", "project")


@pytest.mark.vcr
async def test_resolve_scope_kind_missing(
    client: GitLabClient, auth: GitLabAuth
) -> None:
    with pytest.raises(GitLabNotFound):
        await client.resolve_scope_kind(auth, "definitely/does-not-exist-xyz-123")
