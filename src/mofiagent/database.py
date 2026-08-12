import hashlib
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, LiteralString, cast

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg.sql import SQL
from psycopg_pool import AsyncConnectionPool


async def _check_connection(connection: AsyncConnection[dict[str, Any]]) -> None:
    await connection.execute("SELECT 1")


class Database:
    """Owns the bounded process-level PostgreSQL connection pool."""

    def __init__(
        self,
        dsn: str,
        *,
        min_size: int,
        max_size: int,
        timeout_seconds: float,
    ) -> None:
        self._pool = AsyncConnectionPool[AsyncConnection[dict[str, Any]]](
            conninfo=dsn,
            min_size=min_size,
            max_size=max_size,
            timeout=timeout_seconds,
            max_waiting=max_size * 2,
            open=False,
            kwargs={
                "autocommit": False,
                "row_factory": dict_row,
                "connect_timeout": max(1, int(timeout_seconds)),
            },
            check=_check_connection,
            name="mofiagent",
        )

    async def open(self) -> None:
        await self._pool.open(wait=True)

    async def close(self) -> None:
        await self._pool.close()

    async def check(self) -> None:
        await self._pool.check()

    @asynccontextmanager
    async def connection(self) -> AsyncGenerator[AsyncConnection[dict[str, Any]]]:
        async with self._pool.connection() as connection:
            yield connection


def _migration_files(migrations_dir: Path) -> list[Path]:
    migration_files = sorted(migrations_dir.glob("[0-9][0-9][0-9]_*.sql"))
    if not migration_files:
        raise ValueError(f"no migrations found in {migrations_dir}")
    return migration_files


async def apply_migrations(dsn: str, migrations_dir: Path) -> list[str]:
    """Apply immutable SQL migrations exactly once under an advisory lock."""

    applied: list[str] = []
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=False) as connection:
        await connection.execute("SELECT pg_advisory_xact_lock(%s)", (739_104_221,))
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
            """
        )

        rows = await connection.execute("SELECT version, checksum FROM schema_migrations")
        existing = {row[0]: row[1] async for row in rows}

        for migration_file in _migration_files(migrations_dir):
            sql_text = migration_file.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql_text.encode()).hexdigest()
            prior_checksum = existing.get(migration_file.name)
            if prior_checksum is not None:
                if prior_checksum != checksum:
                    raise RuntimeError(f"applied migration {migration_file.name} has been modified")
                continue

            await connection.execute(SQL(cast(LiteralString, sql_text)))
            await connection.execute(
                "INSERT INTO schema_migrations (version, checksum) VALUES (%s, %s)",
                (migration_file.name, checksum),
            )
            applied.append(migration_file.name)

        await connection.commit()
    return applied
