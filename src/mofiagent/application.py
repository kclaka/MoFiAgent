from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from mofiagent.agent.models import AgentResult, ConversationExchange
from mofiagent.api.models import QuestionResponse, SourceReference
from mofiagent.rates.repository import InteractionRecord, SessionCompletion, SessionTurn
from mofiagent.rates.treasury import TREASURY_SOURCE_NAME


class QuestionProcessingError(RuntimeError):
    """A question failed after its audit record was persisted."""


class AnsweringAgent(Protocol):
    @property
    def model_name(self) -> str: ...

    async def answer(
        self,
        question: str,
        *,
        history: Sequence[ConversationExchange] = (),
    ) -> AgentResult: ...


class ConversationStore(Protocol):
    async def claim_turn(
        self,
        *,
        requested_session_id: UUID | None,
        interaction_id: UUID,
        claimed_at: datetime,
    ) -> SessionTurn: ...

    async def complete_turn(
        self,
        record: InteractionRecord,
        *,
        completed_at: datetime,
    ) -> SessionCompletion: ...


class QuestionService:
    def __init__(
        self,
        *,
        agent: AnsweringAgent,
        conversations: ConversationStore,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        timer: Callable[[], float] = perf_counter,
        id_factory: Callable[[], UUID] = uuid4,
    ) -> None:
        self._agent = agent
        self._conversations = conversations
        self._clock = clock
        self._timer = timer
        self._id_factory = id_factory

    async def answer(self, question: str, *, session_id: UUID | None = None) -> QuestionResponse:
        interaction_id = self._id_factory()
        created_at = self._clock()
        started = self._timer()
        session = await self._conversations.claim_turn(
            requested_session_id=session_id,
            interaction_id=interaction_id,
            claimed_at=created_at,
        )
        try:
            result = await self._agent.answer(question, history=session.history)
        except Exception as error:
            await self._conversations.complete_turn(
                InteractionRecord(
                    id=interaction_id,
                    session_id=session.session_id,
                    turn_number=session.turn_number,
                    created_at=created_at,
                    question=question,
                    answer=None,
                    status="failed",
                    data_as_of=None,
                    model_name=self._agent.model_name,
                    latency_ms=self._latency_ms(started),
                    error_code=type(error).__name__,
                ),
                completed_at=self._clock(),
            )
            raise QuestionProcessingError("question processing failed") from error

        completion = await self._conversations.complete_turn(
            InteractionRecord(
                id=interaction_id,
                session_id=session.session_id,
                turn_number=session.turn_number,
                created_at=created_at,
                question=question,
                answer=result.answer,
                status=result.status,
                tool_calls=result.tool_calls,
                data_as_of=result.data_as_of,
                source_urls=result.source_urls,
                model_name=self._agent.model_name,
                latency_ms=self._latency_ms(started),
            ),
            completed_at=self._clock(),
        )
        source = None
        if result.source_urls:
            source = SourceReference.model_validate(
                {"name": TREASURY_SOURCE_NAME, "url": result.source_urls[0]}
            )
        return QuestionResponse(
            id=interaction_id,
            session_id=completion.session_id,
            turn_number=completion.turn_number,
            session_status=completion.status,
            next_session_id=completion.next_session_id,
            session_restarted=session.restarted,
            question=question,
            answer=result.answer,
            data_as_of=result.data_as_of,
            source=source,
        )

    def _latency_ms(self, started: float) -> int:
        return max(0, round((self._timer() - started) * 1000))
