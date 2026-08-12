from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from mofiagent.agent.models import ToolCallRecord
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
        tool_calls = [item.model_dump(mode="json") for item in record.tool_calls]
        async with self._database.connection() as connection:
            await connection.execute(
                """
                INSERT INTO interaction_history (
                    id,
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
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    record.id,
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

    async def recent(self, *, limit: int) -> list[InteractionRecord]:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        async with self._database.connection() as connection:
            cursor = await connection.execute(
                """
                SELECT
                    id,
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
