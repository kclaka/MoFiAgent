from collections.abc import Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from mofiagent.agent.models import AgentResult
from mofiagent.api.models import QuestionResponse, SourceReference
from mofiagent.rates.repository import InteractionRecord
from mofiagent.rates.treasury import TREASURY_SOURCE_NAME


class QuestionProcessingError(RuntimeError):
    """A question failed after its audit record was persisted."""


class AnsweringAgent(Protocol):
    @property
    def model_name(self) -> str: ...

    async def answer(self, question: str) -> AgentResult: ...


class InteractionWriter(Protocol):
    async def save(self, record: InteractionRecord) -> None: ...


class QuestionService:
    def __init__(
        self,
        *,
        agent: AnsweringAgent,
        interactions: InteractionWriter,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._agent = agent
        self._interactions = interactions
        self._clock = clock
        self._timer = timer
        self._id_factory = id_factory

    async def answer(self, question: str) -> QuestionResponse:
        interaction_id = self._id_factory()
        created_at = self._clock()
        started = self._timer()
        try:
            result = await self._agent.answer(question)
        except Exception as error:
            await self._interactions.save(
                InteractionRecord(
                    id=interaction_id,
                    created_at=created_at,
                    question=question,
                    answer=None,
                    status="failed",
                    data_as_of=None,
                    model_name=self._agent.model_name,
                    latency_ms=self._latency_ms(started),
                    error_code=type(error).__name__,
                )
            )
            raise QuestionProcessingError("question processing failed") from error

        await self._interactions.save(
            InteractionRecord(
                id=interaction_id,
                created_at=created_at,
                question=question,
                answer=result.answer,
                status=result.status,
                tool_calls=result.tool_calls,
                data_as_of=result.data_as_of,
                source_urls=result.source_urls,
                model_name=self._agent.model_name,
                latency_ms=self._latency_ms(started),
            )
        )
        source = None
        if result.source_urls:
            source = SourceReference.model_validate(
                {"name": TREASURY_SOURCE_NAME, "url": result.source_urls[0]}
            )
        return QuestionResponse(
            id=interaction_id,
            question=question,
            answer=result.answer,
            data_as_of=result.data_as_of,
            source=source,
        )

    def _latency_ms(self, started: float) -> int:
        return max(0, round((self._timer() - started) * 1000))
