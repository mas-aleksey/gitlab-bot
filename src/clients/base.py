from __future__ import annotations

from typing import Any

import backoff
import httpx
from loguru import logger

_MAX_ATTEMPTS = 3
_RETRY_STATUS = frozenset({429, 502, 503, 504})


type QueryParams = dict[str, Any] | list[tuple[str, Any]] | None


class ApiError(Exception):
    """Any transport- or HTTP-level failure from an external API."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_text: str = "",
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_text = response_text


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code in _RETRY_STATUS
    return False


class BaseClient:
    """Async HTTP client with keep-alive, retry, and JSON helpers.

    One instance per application: reuses a single ``httpx.AsyncClient``
    (connection pool + HTTP/2). Concrete clients (``GitLabClient`` etc.)
    subclass or wrap it and add domain methods.
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self.client = client or httpx.AsyncClient(
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20),
            timeout=httpx.Timeout(30.0, connect=5.0),
            http2=True,
        )

    async def __aenter__(self) -> BaseClient:
        return self

    async def __aexit__(self, exc_type: object, exc: object, tb: object) -> None:
        await self.stop()

    async def stop(self) -> None:
        await self.client.aclose()

    @backoff.on_exception(
        backoff.expo,
        (httpx.TransportError, httpx.HTTPStatusError),
        max_tries=_MAX_ATTEMPTS,
        giveup=lambda e: not _is_retryable(e),
    )
    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: QueryParams = None,
        json: dict[str, Any] | None = None,
    ) -> httpx.Response:
        resp = await self.client.request(
            method, url, headers=headers, params=params, json=json
        )
        logger.info(
            "{} response: [{}] {} {}",
            self.__class__.__name__, resp.status_code, method, url,
        )
        resp.raise_for_status()
        return resp
