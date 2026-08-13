import os
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from uuid import uuid4

import pytest

from mofiagent.agent.models import ToolCallRecord
from mofiagent.database import Database, apply_migrations
from mofiagent.rates.ingestion import IngestionService
from mofiagent.rates.repository import (
    ConversationRepository,
    IngestionRepository,
    InteractionRecord,
    InteractionRepository,
    RateNotFoundError,
    RateRepository,
    SessionBusyError,
    SessionLeaseLostError,
    SessionNotFoundError,
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

        async with database.connection() as connection:
            privileges = await connection.execute(
                """
                SELECT
                    has_table_privilege(
                        'mofi_api', 'conversations', 'SELECT'
                    ) AS api_can_read_conversations,
                    has_table_privilege(
                        'mofi_api', 'interaction_history', 'INSERT'
                    ) AS api_can_write_history,
                    has_table_privilege(
                        'mofi_ingest', 'rate_observations', 'UPDATE'
                    ) AS ingest_can_update_rates
                """
            )
            assert await privileges.fetchone() == {
                "api_can_read_conversations": True,
                "api_can_write_history": True,
                "ingest_can_update_rates": True,
            }
            duplicate_index = await connection.execute(
                """
                SELECT count(*) AS count
                FROM pg_indexes
                WHERE indexname = 'interaction_history_session_history_idx'
                """
            )
            duplicate_index_row = await duplicate_index.fetchone()
            assert duplicate_index_row is not None
            assert duplicate_index_row["count"] == 0
    finally:
        await database.close()


def test_fixture_itself_remains_valid() -> None:
    observations = parse_treasury_feed(FIXTURE.read_bytes(), source_url=SOURCE_URL)
    assert len(observations) == 28


@pytest.mark.asyncio
async def test_five_turn_conversation_closes_and_rolls_to_fresh_session() -> None:
    dsn = _database_dsn()
    await apply_migrations(dsn, MIGRATIONS)
    database = Database(dsn, min_size=1, max_size=2, timeout_seconds=5)
    await database.open()
    try:
        conversations = ConversationRepository(database)
        first_interaction_id = uuid4()
        started_at = datetime.now(UTC)
        turn = await conversations.claim_turn(
            requested_session_id=None,
            interaction_id=first_interaction_id,
            claimed_at=started_at,
        )
        first_session_id = turn.session_id
        assert turn.turn_number == 1
        assert turn.history == []

        with pytest.raises(SessionBusyError):
            await conversations.claim_turn(
                requested_session_id=first_session_id,
                interaction_id=uuid4(),
                claimed_at=started_at,
            )

        interaction_id = first_interaction_id
        completion = None
        for turn_number in range(1, 6):
            if turn_number > 1:
                interaction_id = uuid4()
                turn = await conversations.claim_turn(
                    requested_session_id=first_session_id,
                    interaction_id=interaction_id,
                    claimed_at=datetime.now(UTC),
                )
                assert turn.turn_number == turn_number
                assert len(turn.history) == turn_number - 1

            completion = await conversations.complete_turn(
                InteractionRecord(
                    id=interaction_id,
                    session_id=turn.session_id,
                    turn_number=turn.turn_number,
                    created_at=datetime.now(UTC),
                    question=f"Question {turn_number}",
                    answer=f"Answer {turn_number}",
                    status="answered",
                    model_name="integration-test-model",
                    latency_ms=10,
                    data_as_of=None,
                ),
                completed_at=datetime.now(UTC),
            )

        assert completion is not None
        assert completion.status == "closed"
        assert completion.next_session_id is not None

        rollover = await conversations.claim_turn(
            requested_session_id=first_session_id,
            interaction_id=uuid4(),
            claimed_at=datetime.now(UTC),
        )
        assert rollover.session_id == completion.next_session_id
        assert rollover.turn_number == 1
        assert rollover.history == []
        assert rollover.restarted is True

        with pytest.raises(SessionNotFoundError):
            await conversations.claim_turn(
                requested_session_id=uuid4(),
                interaction_id=uuid4(),
                claimed_at=datetime.now(UTC),
            )
    finally:
        await database.close()


@pytest.mark.asyncio
async def test_expired_session_lease_can_be_reclaimed_without_old_writer_winning() -> None:
    dsn = _database_dsn()
    await apply_migrations(dsn, MIGRATIONS)
    database = Database(dsn, min_size=1, max_size=2, timeout_seconds=5)
    await database.open()
    try:
        conversations = ConversationRepository(database, lease_seconds=2)
        started_at = datetime.now(UTC)
        stale_interaction_id = uuid4()
        stale = await conversations.claim_turn(
            requested_session_id=None,
            interaction_id=stale_interaction_id,
            claimed_at=started_at,
        )
        replacement_interaction_id = uuid4()
        replacement = await conversations.claim_turn(
            requested_session_id=stale.session_id,
            interaction_id=replacement_interaction_id,
            claimed_at=started_at + timedelta(seconds=3),
        )

        with pytest.raises(SessionLeaseLostError):
            await conversations.complete_turn(
                InteractionRecord(
                    id=stale_interaction_id,
                    session_id=stale.session_id,
                    turn_number=stale.turn_number,
                    created_at=started_at,
                    question="Stale question",
                    answer="Stale answer",
                    status="answered",
                    model_name="integration-test-model",
                    latency_ms=3000,
                    data_as_of=None,
                ),
                completed_at=datetime.now(UTC),
            )

        completion = await conversations.complete_turn(
            InteractionRecord(
                id=replacement_interaction_id,
                session_id=replacement.session_id,
                turn_number=replacement.turn_number,
                created_at=started_at,
                question="Replacement question",
                answer="Replacement answer",
                status="answered",
                model_name="integration-test-model",
                latency_ms=10,
                data_as_of=None,
            ),
            completed_at=datetime.now(UTC),
        )
        assert completion.turn_number == 1
    finally:
        await database.close()
