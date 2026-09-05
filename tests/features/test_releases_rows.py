"""Агрегация строк релиз-экрана: что считается запускаемым и перезапускаемым."""

from clients.gitlab import JobRef
from features.releases.service import _aggregate


def _ref(job_id: int, status: str) -> JobRef:
    return JobRef(
        project_id=1,
        pipeline_id=2,
        job_id=job_id,
        name="deploy",
        stage="release",
        status=status,
        node_label="svc",
    )


def test_failed_and_canceled_are_retryable() -> None:
    row = _aggregate(
        "deploy",
        [
            _ref(1, "manual"),
            _ref(2, "failed"),
            _ref(3, "canceled"),
            _ref(4, "success"),
            _ref(5, "running"),
            _ref(6, "skipped"),
        ],
        {"release": 0},
    )
    assert row.ready == 1
    assert row.failed == 2
    assert [r.job_id for r in row.playable] == [1]
    assert [r.job_id for r in row.retryable] == [2, 3]
    assert row.total == 6


def test_nothing_to_retry_when_all_green() -> None:
    row = _aggregate("deploy", [_ref(1, "success"), _ref(2, "skipped")], {"release": 0})
    assert row.retryable == ()
    assert row.playable == ()
