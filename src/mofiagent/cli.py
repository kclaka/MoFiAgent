import argparse
import asyncio
from collections.abc import Sequence
from datetime import UTC, datetime

import uvicorn

from mofiagent.config import get_settings
from mofiagent.database import Database, apply_migrations
from mofiagent.rates.ingestion import IngestionService
from mofiagent.rates.repository import IngestionRepository, RateRepository
from mofiagent.rates.treasury import TreasuryClient
from mofiagent.telemetry.logging import configure_logging


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="mofiagent")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("serve", help="Run the HTTP API")
    subcommands.add_parser("migrate", help="Apply database migrations")
    ingest_parser = subcommands.add_parser("ingest", help="Ingest Treasury rate observations")
    ingest_parser.add_argument(
        "--year",
        type=int,
        default=datetime.now(UTC).year,
        help="Treasury calendar year to ingest (default: current UTC year)",
    )
    return parser


def _database_from_settings() -> Database:
    settings = get_settings()
    settings.validate_for_database()
    assert settings.database_dsn is not None
    return Database(
        settings.database_dsn.get_secret_value(),
        min_size=settings.db_pool_min_size,
        max_size=settings.db_pool_max_size,
        timeout_seconds=settings.db_pool_timeout_seconds,
    )


async def _run_migrations() -> None:
    settings = get_settings()
    settings.validate_for_database()
    assert settings.database_dsn is not None
    applied = await apply_migrations(
        settings.database_dsn.get_secret_value(),
        settings.migrations_dir,
    )
    if applied:
        print(f"Applied {len(applied)} migration(s): {', '.join(applied)}")
    else:
        print("Database schema is current")


async def _run_ingestion(year: int) -> None:
    settings = get_settings()
    database = _database_from_settings()
    await database.open()
    try:
        service = IngestionService(
            client=TreasuryClient(timeout_seconds=settings.request_timeout_seconds),
            rates=RateRepository(database),
            runs=IngestionRepository(database),
        )
        result = await service.run(year)
    finally:
        await database.close()
    print(
        f"Ingestion {result.status}: {result.rows_upserted}/{result.rows_fetched} rates "
        f"stored for {result.year}; run_id={result.id}"
    )


def main(argv: Sequence[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level)

    if args.command == "serve":
        uvicorn.run(
            "mofiagent.api.app:app",
            host=settings.host,
            port=settings.port,
            workers=1,
            proxy_headers=True,
        )
        return

    if args.command == "migrate":
        asyncio.run(_run_migrations())
        return

    if args.command == "ingest":
        asyncio.run(_run_ingestion(args.year))
        return

    raise AssertionError(f"Unhandled command: {args.command}")
