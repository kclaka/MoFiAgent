from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict

from mofiagent.rates.repository import IngestionRepository, RateRepository
from mofiagent.rates.treasury import TreasuryClient, build_feed_url, parse_treasury_feed


class IngestionResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: UUID
    year: int
    status: str
    source_url: str
    rows_fetched: int
    rows_upserted: int
    started_at: datetime
    completed_at: datetime


class Clock(Protocol):
    def __call__(self) -> datetime: ...


class IngestionService:
    def __init__(
        self,
        *,
        client: TreasuryClient,
        rates: RateRepository,
        runs: IngestionRepository,
        clock: Clock = lambda: datetime.now(UTC),
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._client = client
        self._rates = rates
        self._runs = runs
        self._clock = clock
        self._id_factory = id_factory

    async def run(self, year: int, *, allow_empty: bool = False) -> IngestionResult:
        expected_url = build_feed_url(year)
        run_id = self._id_factory()
        started_at = self._clock()
        await self._runs.start(run_id, started_at, expected_url)

        try:
            source_url, payload = await self._client.fetch_year(year)
            observations = parse_treasury_feed(
                payload,
                source_url=source_url,
                allow_empty=allow_empty,
            )
            rows_upserted = await self._rates.upsert_observations(observations)
            completed_at = self._clock()
            await self._runs.succeed(
                run_id,
                completed_at=completed_at,
                source_url=source_url,
                rows_fetched=len(observations),
                rows_upserted=rows_upserted,
            )
        except Exception as error:
            await self._runs.fail(
                run_id,
                completed_at=self._clock(),
                error_message=f"{type(error).__name__}: {error}",
            )
            raise

        return IngestionResult(
            id=run_id,
            year=year,
            status="succeeded",
            source_url=source_url,
            rows_fetched=len(observations),
            rows_upserted=rows_upserted,
            started_at=started_at,
            completed_at=completed_at,
        )
