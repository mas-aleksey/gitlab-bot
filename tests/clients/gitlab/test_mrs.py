import pytest

from clients.gitlab import GitLabAuth, GitLabClient


@pytest.mark.vcr
async def test_list_authored_open_mrs(
    client: GitLabClient, auth: GitLabAuth
) -> None:
    items = await client.list_authored_open_mrs(auth)
    for m in items:
        assert m.mr_iid > 0
        assert m.sha


@pytest.mark.vcr
async def test_list_open_mrs_in_scope(
    client: GitLabClient, auth: GitLabAuth, scope_path: str
) -> None:
    kind = await client.resolve_scope_kind(auth, scope_path)
    items = await client.list_open_mrs_in_scope(
        auth, scope_path=scope_path, scope_kind=kind
    )
    for m in items:
        assert m.mr_iid > 0
