from collections.abc import Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Literal, Protocol
from uuid import UUID, uuid4

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from mofiagent.agent.models import ConversationExchange, ToolCallRecord
from mofiagent.database import Database
from mofiagent.rates.models import (
    SUPPORTED_TENORS_MONTHS,
    TREASURY_NOMINAL_SERIES,
    CurveSpread,
    RateComparison,
    RateObservation,
    RatePoint,
)


class RateNotFoundError(LookupError):
    pass


class InteractionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    session_id: UUID | None = None
    turn_number: int | None = Field(default=None, ge=1, le=5)
    created_at: datetime
    question: str
    answer: str | None
    status: Literal["answered", "unsupported", "failed"]
    tool_calls: list[ToolCallRecord] = Field(default_factory=list[ToolCallRecord])
    data_as_of: date | None
    source_urls: list[str] = Field(default_factory=list[str])
    model_name: str
    latency_ms: int
    error_code: str | None = None


class SessionTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    turn_number: int = Field(ge=1, le=5)
    history: list[ConversationExchange]
    restarted: bool


class SessionCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)

    session_id: UUID
    turn_number: int = Field(ge=1, le=5)
    status: Literal["active", "closed"]
    next_session_id: UUID | None = None


class SessionNotFoundError(LookupError):
    pass


class SessionBusyError(RuntimeError):
    pass


class SessionLeaseLostError(RuntimeError):
    pass


class UUIDFactory(Protocol):
    def __call__(self) -> UUID: ...


MAX_SESSION_TURNS = 5


async def _insert_interaction(
    connection: AsyncConnection[dict[str, Any]],
    record: InteractionRecord,
) -> None:
    tool_calls = [item.model_dump(mode="json") for item in record.tool_calls]
    await connection.execute(
        """
        INSERT INTO interaction_history (
            id,
            session_id,
            turn_number,
            created_at,
            question,
            answer,
            status,
            tool_calls,
            data_as_of,
            source_urls,
            model_name,
            latency_ms,
            error_code
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            record.id,
            record.session_id,
            record.turn_number,
            record.created_at,
            record.question,
            record.answer,
            record.status,
            Jsonb(tool_calls),
            record.data_as_of,
            Jsonb(record.source_urls),
            record.model_name,
            record.latency_ms,
            record.error_code,
        ),
    )


class ConversationRepository:
    """Owns durable five-turn sessions and their single-request lease."""

    def __init__(
        self,
        database: Database,
        *,
        lease_seconds: int = 60,
        id_factory: UUIDFactory = uuid4,
    ) -> None:
        if lease_seconds < 1:
            raise ValueError("lease_seconds must be positive")
        self._database = database
        self._lease_seconds = lease_seconds
        self._id_factory = id_factory

    async def claim_turn(
        self,
        *,
        requested_session_id: UUID | None,
        interaction_id: UUID,
        claimed_at: datetime,
    ) -> SessionTurn:
        restarted = False
        async with self._database.connection() as connection:
            if requested_session_id is None:
                session_id = self._id_factory()
                await connection.execute(
                    """
                    INSERT INTO conversations (
                        id,
                        created_at,
                        pending_interaction_id,
                        pending_started_at
                    )
                    VALUES (%s, %s, %s, %s)
                    """,
                    (session_id, claimed_at, interaction_id, claimed_at),
                )
                completed_turns = 0
            else:
                session_id = requested_session_id
                while True:
                    cursor = await connection.execute(
                        """
                        SELECT
                            status,
                            completed_turns,
                            next_session_id,
                            pending_interaction_id,
                            pending_started_at
                        FROM conversations
                        WHERE id = %s
                        FOR UPDATE
                        """,
                        (session_id,),
                    )
                    row = await cursor.fetchone()
                    if row is None:
                        raise SessionNotFoundError("session was not found")
                    if row["status"] == "closed":
                        next_session_id = row["next_session_id"]
                        if not isinstance(next_session_id, UUID):
                            raise RuntimeError("closed session has no successor")
                        session_id = next_session_id
                        restarted = True
                        continue

                    pending_id = row["pending_interaction_id"]
                    pending_started_at = row["pending_started_at"]
                    lease_cutoff = claimed_at - timedelta(seconds=self._lease_seconds)
                    if (
                        pending_id is not None
                        and isinstance(pending_started_at, datetime)
                        and pending_started_at >= lease_cutoff
                    ):
                        raise SessionBusyError(
                            "another question is already running for this session"
                        )

                    completed_turns = int(row["completed_turns"])
                    await connection.execute(
                        """
                        UPDATE conversations
                        SET pending_interaction_id = %s,
                            pending_started_at = %s
                        WHERE id = %s
                        """,
                        (interaction_id, claimed_at, session_id),
                    )
                    break

            cursor = await connection.execute(
                """
                SELECT question, answer
                FROM interaction_history
                WHERE session_id = %s
                    AND status IN ('answered', 'unsupported')
                    AND answer IS NOT NULL
                ORDER BY turn_number
                """,
                (session_id,),
            )
            rows = await cursor.fetchall()

        history = [
            ConversationExchange(question=str(row["question"]), answer=str(row["answer"]))
            for row in rows
        ]
        return SessionTurn(
            session_id=session_id,
            turn_number=completed_turns + 1,
            history=history,
            restarted=restarted,
        )

    async def complete_turn(
        self,
        record: InteractionRecord,
        *,
        completed_at: datetime,
    ) -> SessionCompletion:
        if record.session_id is None or record.turn_number is None:
            raise ValueError("session_id and turn_number are required")

        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT status, completed_turns, pending_interaction_id
                FROM conversations
                WHERE id = %s
                FOR UPDATE
                """,
                (record.session_id,),
            )
            row = await cursor.fetchone()
            if (
                row is None
                or row["status"] != "active"
                or row["pending_interaction_id"] != record.id
                or int(row["completed_turns"]) + 1 != record.turn_number
            ):
                raise SessionLeaseLostError("session turn lease is no longer active")

            await _insert_interaction(connection, record)
            if record.turn_number == MAX_SESSION_TURNS:
                next_session_id = self._id_factory()
                await connection.execute(
                    """
                    INSERT INTO conversations (id, created_at)
                    VALUES (%s, %s)
                    """,
                    (next_session_id, completed_at),
                )
                await connection.execute(
                    """
                    UPDATE conversations
                    SET completed_turns = %s,
                        status = 'closed',
                        closed_at = %s,
                        next_session_id = %s,
                        pending_interaction_id = NULL,
                        pending_started_at = NULL
                    WHERE id = %s
                    """,
                    (
                        record.turn_number,
                        completed_at,
                        next_session_id,
                        record.session_id,
                    ),
                )
                return SessionCompletion(
                    session_id=record.session_id,
                    turn_number=record.turn_number,
                    status="closed",
                    next_session_id=next_session_id,
                )

            await connection.execute(
                """
                UPDATE conversations
                SET completed_turns = %s,
                    pending_interaction_id = NULL,
                    pending_started_at = NULL
                WHERE id = %s
                """,
                (record.turn_number, record.session_id),
            )

        return SessionCompletion(
            session_id=record.session_id,
            turn_number=record.turn_number,
            status="active",
        )


class RateRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def upsert_observations(self, observations: Sequence[RateObservation]) -> int:
        if not observations:
            return 0

        values = [
            (
                item.observed_at,
                item.series,
                item.tenor_months,
                item.rate_percent,
                item.source_url,
            )
            for item in observations
        ]
        async with self._database.connection() as connection, connection.cursor() as cursor:
            await cursor.executemany(
                """
                    INSERT INTO rate_observations (
                        observed_at,
                        series,
                        tenor_months,
                        rate_percent,
                        source_url
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (observed_at, series, tenor_months)
                    DO UPDATE SET
                        rate_percent = EXCLUDED.rate_percent,
                        source_url = EXCLUDED.source_url,
                        ingested_at = now()
                """,
                values,
            )
        return len(values)

    async def latest(self, tenor_months: Decimal) -> RatePoint:
        self._validate_tenor(tenor_months)
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT observed_at, series, tenor_months, rate_percent, source_url
                FROM rate_observations
                WHERE series = %s AND tenor_months = %s
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (TREASURY_NOMINAL_SERIES, tenor_months),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RateNotFoundError(f"no rate found for {tenor_months} months")
        return RatePoint.model_validate(row)

    async def on_or_before(self, tenor_months: Decimal, requested_date: date) -> RatePoint:
        self._validate_tenor(tenor_months)
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT observed_at, series, tenor_months, rate_percent, source_url
                FROM rate_observations
                WHERE series = %s
                    AND tenor_months = %s
                    AND observed_at <= %s
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (TREASURY_NOMINAL_SERIES, tenor_months, requested_date),
            )
            row = await cursor.fetchone()
        if row is None:
            raise RateNotFoundError(
                f"no rate found for {tenor_months} months on or before {requested_date}"
            )
        return RatePoint.model_validate(row)

    async def compare(
        self,
        tenor_months: Decimal,
        start_date: date,
        end_date: date,
    ) -> RateComparison:
        if end_date < start_date:
            raise ValueError("end_date cannot precede start_date")
        start = await self.on_or_before(tenor_months, start_date)
        end = await self.on_or_before(tenor_months, end_date)
        point_change = end.rate_percent - start.rate_percent
        return RateComparison(
            start=start,
            end=end,
            percentage_point_change=point_change,
            basis_point_change=point_change * Decimal("100"),
        )

    async def spread(
        self,
        short_tenor_months: Decimal,
        long_tenor_months: Decimal,
        requested_date: date | None = None,
    ) -> CurveSpread:
        self._validate_tenor(short_tenor_months)
        self._validate_tenor(long_tenor_months)
        if short_tenor_months >= long_tenor_months:
            raise ValueError("short tenor must be less than long tenor")

        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT observed_at
                FROM rate_observations
                WHERE series = %s
                    AND tenor_months IN (%s, %s)
                    AND (%s::date IS NULL OR observed_at <= %s::date)
                GROUP BY observed_at
                HAVING count(DISTINCT tenor_months) = 2
                ORDER BY observed_at DESC
                LIMIT 1
                """,
                (
                    TREASURY_NOMINAL_SERIES,
                    short_tenor_months,
                    long_tenor_months,
                    requested_date,
                    requested_date,
                ),
            )
            date_row = await cursor.fetchone()
            if date_row is None:
                raise RateNotFoundError("no common observation date found for the requested tenors")
            common_date = date_row["observed_at"]

            cursor = await connection.execute(
                """
                SELECT observed_at, series, tenor_months, rate_percent, source_url
                FROM rate_observations
                WHERE series = %s
                    AND observed_at = %s
                    AND tenor_months IN (%s, %s)
                ORDER BY tenor_months
                """,
                (
                    TREASURY_NOMINAL_SERIES,
                    common_date,
                    short_tenor_months,
                    long_tenor_months,
                ),
            )
            rows = await cursor.fetchall()

        points = {Decimal(str(row["tenor_months"])): RatePoint.model_validate(row) for row in rows}
        short_rate = points[short_tenor_months]
        long_rate = points[long_tenor_months]
        return CurveSpread(
            observed_at=common_date,
            short_rate=short_rate,
            long_rate=long_rate,
            spread_basis_points=(long_rate.rate_percent - short_rate.rate_percent) * Decimal("100"),
        )

    async def latest_observation_date(self) -> date | None:
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                "SELECT max(observed_at) AS observed_at FROM rate_observations"
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        value = row["observed_at"]
        return value if isinstance(value, date) else None

    @staticmethod
    def _validate_tenor(tenor_months: Decimal) -> None:
        if tenor_months not in SUPPORTED_TENORS_MONTHS:
            raise ValueError(f"unsupported Treasury tenor: {tenor_months} months")


class IngestionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def start(self, run_id: UUID, started_at: datetime, source_url: str) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO ingestion_runs (id, started_at, status, source_url)
                VALUES (%s, %s, 'running', %s)
                """,
                (run_id, started_at, source_url),
            )

    async def succeed(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        source_url: str,
        rows_fetched: int,
        rows_upserted: int,
    ) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE ingestion_runs
                SET completed_at = %s,
                    status = 'succeeded',
                    source_url = %s,
                    rows_fetched = %s,
                    rows_upserted = %s
                WHERE id = %s AND status = 'running'
                """,
                (completed_at, source_url, rows_fetched, rows_upserted, run_id),
            )

    async def fail(
        self,
        run_id: UUID,
        *,
        completed_at: datetime,
        error_message: str,
    ) -> None:
        async with self._database.connection() as connection:
            await connection.execute(
                """
                UPDATE ingestion_runs
                SET completed_at = %s,
                    status = 'failed',
                    error_message = %s
                WHERE id = %s AND status = 'running'
                """,
                (completed_at, error_message[:1000], run_id),
            )


class InteractionRepository:
    def __init__(self, database: Database) -> None:
        self._database = database

    async def save(self, record: InteractionRecord) -> None:
        async with self._database.connection() as connection:
            await _insert_interaction(connection, record)

    async def recent(self, *, limit: int) -> list[InteractionRecord]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    id,
                    session_id,
                    turn_number,
                    created_at,
                    question,
                    answer,
                    status,
                    tool_calls,
                    data_as_of,
                    source_urls,
                    model_name,
                    latency_ms,
                    error_code
                FROM interaction_history
                ORDER BY created_at DESC, id DESC
                LIMIT %s
                """,
                (limit,),
            )
            rows = await cursor.fetchall()
        return [InteractionRecord.model_validate(row) for row in rows]
