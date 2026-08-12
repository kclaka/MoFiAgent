from typing import Any, cast

import psycopg
import pytest
from psycopg import AsyncConnection

from mofiagent import database


@pytest.mark.asyncio
async def test_startup_connection_retries_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts = 0
    sentinel = cast(AsyncConnection[Any], object())

    async def connect(*args: Any, **kwargs: Any) -> AsyncConnection[Any]:
        nonlocal attempts
        attempts += 1
        assert kwargs["connect_timeout"] == 7
        if attempts == 1:
            raise psycopg.OperationalError("transient")
        return sentinel

    async def no_sleep(delay: float) -> None:
        assert delay == 1

    monkeypatch.setattr(database.psycopg.AsyncConnection, "connect", connect)
    monkeypatch.setattr(database.asyncio, "sleep", no_sleep)

    connection = await database.connect_with_retry(
        "postgresql://example",
        connect_timeout_seconds=7,
        startup_timeout_seconds=30,
    )

    assert connection is sentinel
    assert attempts == 2
