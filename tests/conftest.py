import os
from collections.abc import AsyncIterator

import pytest
from dotenv import load_dotenv
from tests.scrub import FAKE_PROJECT_PATH, FAKE_SCOPE_PATH, scrub_response

from clients.gitlab import GitLabAuth, GitLabClient

load_dotenv()


@pytest.fixture(scope="session")
def vcr_config() -> dict[str, object]:
    """Keep private data out of the cassettes, which ship in a public repo.

    `filter_headers` alone only covers the PAT: response *bodies* hold usernames,
    emails, real names, project paths and the instance host, gzipped and base64'd
    so no grep over the cassette files would ever show them. `before_record_response`
    runs the same anonymiser as `python -m tests.scrub`, so a newly recorded
    cassette is already scrubbed on disk.
    """
    return {
        "filter_headers": [("PRIVATE-TOKEN", "***REDACTED***")],
        "filter_query_parameters": ["private_token"],
        "before_record_response": scrub_response,
        "record_mode": "once",
    }


@pytest.fixture
def gitlab_url() -> str:
    # Recording needs the real instance; replay must use the scrubbed host, because
    # VCR matches on URL and the cassettes carry the anonymised one.
    return os.getenv("GITLAB_URL", "https://gl.example.com")


@pytest.fixture
def scope_path() -> str:
    # Defaults mirror what tests/scrub.py rewrites the recorded paths into — change
    # one without the other and every recorded request stops matching on replay.
    return os.getenv("GITLAB_SCOPE_PATH", FAKE_SCOPE_PATH)


@pytest.fixture
def test_project_path() -> str:
    return os.getenv("GITLAB_TEST_PROJECT_PATH", FAKE_PROJECT_PATH)


@pytest.fixture
def auth(gitlab_url: str) -> GitLabAuth:
    # Real PAT is used only during initial cassette recording; on replay
    # any non-empty value works because pytest-recording matches on URL.
    return GitLabAuth(base_url=gitlab_url, pat=os.getenv("PAT", "test-pat"))


@pytest.fixture
async def client() -> AsyncIterator[GitLabClient]:
    c = GitLabClient()
    try:
        yield c
    finally:
        await c.stop()
