import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from mofiagent.agent.models import ToolCallRecord
from mofiagent.database import Database, apply_migrations
from mofiagent.rates.ingestion import IngestionService
from mofiagent.rates.repository import (
    IngestionRepository,
    InteractionRecord,
    InteractionRepository,
    RateNotFoundError,
    RateRepository,
)
from mofiagent.rates.treasury import TreasuryClient, build_feed_url, parse_treasury_feed

FIXTURE = Path(__file__).parents[1] / "fixtures" / "treasury_yield_curve.xml"
MIGRATIONS = Path(__file__).parents[2] / "migrations"
SOURCE_URL = build_feed_url(2026)


class FixtureTreasuryClient(TreasuryClient):
    async def fetch_year(self, year: int) -> tuple[str, bytes]:
        assert year == 2026
        return SOURCE_URL, FIXTURE.read_bytes()


def _database_dsn() -> str:
    dsn = os.getenv("MOFI_TEST_DATABASE_DSN")
    if not dsn:
        pytest.skip("MOFI_TEST_DATABASE_DSN is not configured")
    return dsn


@pytest.mark.asyncio
async def test_migrations_ingestion_and_exact_rate_queries() -> None:
    dsn = _database_dsn()
    assert await apply_migrations(dsn, MIGRATIONS) == []

    database = Database(dsn, min_size=1, max_size=2, timeout_seconds=5)
    await database.open()
    try:
        rates = RateRepository(database)
        runs = IngestionRepository(database)
        times = iter(
            [
                datetime(2026, 8, 12, 20, 0, tzinfo=UTC),
                datetime(2026, 8, 12, 20, 0, 1, tzinfo=UTC),
            ]
        )
        result = await IngestionService(
            client=FixtureTreasuryClient(),
            rates=rates,
            runs=runs,
            clock=lambda: next(times),
            id_factory=uuid4,
        ).run(2026)

        assert result.rows_fetched == 28
        assert result.rows_upserted == 28
        assert (await rates.latest(Decimal("120"))).rate_percent == Decimal("4.6800")
        assert (
            await rates.on_or_before(Decimal("120"), date(2026, 8, 11))
        ).rate_percent == Decimal("4.7000")

        comparison = await rates.compare(
            Decimal("120"),
            date(2026, 8, 11),
            date(2026, 8, 12),
        )
        assert comparison.basis_point_change == Decimal("-2.0000")

        spread = await rates.spread(Decimal("24"), Decimal("120"))
        assert spread.observed_at == date(2026, 8, 12)
        assert spread.spread_basis_points == Decimal("48.0000")
        assert await rates.latest_observation_date() == date(2026, 8, 12)

        with pytest.raises(RateNotFoundError):
            await rates.on_or_before(Decimal("120"), date(1990, 1, 1))

        interaction_id = uuid4()
        interactions = InteractionRepository(database)
        await interactions.save(
            InteractionRecord(
                id=interaction_id,
                created_at=datetime(2026, 8, 12, 21, 0, tzinfo=UTC),
                question="What's the 10-year Treasury yield?",
                answer="The 10-year Treasury yield was 4.68%.",
                status="answered",
                tool_calls=[
                    ToolCallRecord(
                        name="get_latest_rate",
                        arguments={"tenor": "10y"},
                        success=True,
                    )
                ],
                data_as_of=date(2026, 8, 12),
                source_urls=[SOURCE_URL],
                model_name="integration-test-model",
                latency_ms=25,
            )
        )
        recent = await interactions.recent(limit=100)
        saved = next(item for item in recent if item.id == interaction_id)
        assert saved.tool_calls[0].name == "get_latest_rate"
    finally:
        await database.close()


def test_fixture_itself_remains_valid() -> None:
    observations = parse_treasury_feed(FIXTURE.read_bytes(), source_url=SOURCE_URL)
    assert len(observations) == 28
