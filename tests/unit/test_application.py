import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from mofiagent.agent.models import AgentResult, ConversationExchange, ToolCallRecord
from mofiagent.application import (
    AnsweringAgent,
    ConversationStore,
    QuestionProcessingError,
    QuestionService,
)
from mofiagent.rates.repository import InteractionRecord, SessionCompletion, SessionTurn

SESSION_ID = UUID("00000000-0000-0000-0000-000000000020")
NEXT_SESSION_ID = UUID("00000000-0000-0000-0000-000000000021")


class FakeAgent:
    def __init__(self, result: AgentResult | Exception) -> None:
        self._result = result
        self.history: Sequence[ConversationExchange] = ()

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def answer(
        self,
        question: str,
        *,
        history: Sequence[ConversationExchange] = (),
    ) -> AgentResult:
        assert question
        self.history = history
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class NeverCompletingAgent:
    @property
    def model_name(self) -> str:
        return "fake-model"

    async def answer(
        self,
        question: str,
        *,
        history: Sequence[ConversationExchange] = (),
    ) -> AgentResult:
        assert question
        assert history
        await asyncio.Event().wait()
        raise AssertionError("unreachable")


class FakeConversations:
    def __init__(
        self,
        *,
        turn_number: int = 1,
        restarted: bool = False,
        completion_error: Exception | None = None,
    ) -> None:
        self.records: list[InteractionRecord] = []
        self.turn_number = turn_number
        self.restarted = restarted
        self.completion_error = completion_error
        self.history = [ConversationExchange(question="Initial question", answer="Initial answer")]

    async def claim_turn(
        self,
        *,
        requested_session_id: UUID | None,
        interaction_id: UUID,
        claimed_at: datetime,
    ) -> SessionTurn:
        assert interaction_id
        assert claimed_at
        if requested_session_id is not None:
            assert requested_session_id == SESSION_ID
        return SessionTurn(
            session_id=SESSION_ID,
            turn_number=self.turn_number,
            history=self.history,
            restarted=self.restarted,
        )

    async def complete_turn(
        self,
        record: InteractionRecord,
        *,
        completed_at: datetime,
    ) -> SessionCompletion:
        assert completed_at
        if self.completion_error is not None:
            raise self.completion_error
        self.records.append(record)
        closed = self.turn_number == 5
        return SessionCompletion(
            session_id=SESSION_ID,
            turn_number=self.turn_number,
            status="closed" if closed else "active",
            next_session_id=NEXT_SESSION_ID if closed else None,
        )


def question_service(
    result: AgentResult | Exception,
    *,
    turn_number: int = 1,
    restarted: bool = False,
    completion_error: Exception | None = None,
) -> tuple[QuestionService, FakeConversations, FakeAgent]:
    conversations = FakeConversations(
        turn_number=turn_number,
        restarted=restarted,
        completion_error=completion_error,
    )
    agent = FakeAgent(result)
    timer = iter([10.0, 10.125])
    service = QuestionService(
        agent=cast(AnsweringAgent, agent),
        conversations=cast(ConversationStore, conversations),
        clock=lambda: datetime(2026, 8, 12, 21, 0, tzinfo=UTC),
        timer=lambda: next(timer),
        id_factory=lambda: UUID("00000000-0000-0000-0000-000000000010"),
    )
    return service, conversations, agent


@pytest.mark.asyncio
async def test_question_service_persists_answer() -> None:
    service, conversations, agent = question_service(
        AgentResult(
            answer="The 10-year yield was 4.68% on August 12, 2026.",
            status="answered",
            tool_calls=[
                ToolCallRecord(name="get_latest_rate", arguments={"tenor": "10y"}, success=True)
            ],
            data_as_of=date(2026, 8, 12),
            source_urls=["https://home.treasury.gov/example"],
        )
    )

    response = await service.answer("What's the 10-year yield?", session_id=SESSION_ID)

    assert response.source is not None
    assert str(response.source.url) == "https://home.treasury.gov/example"
    assert response.session_id == SESSION_ID
    assert response.turn_number == 1
    assert response.session_status == "active"
    assert conversations.records[0].status == "answered"
    assert conversations.records[0].latency_ms == 125
    assert agent.history == conversations.history


@pytest.mark.asyncio
async def test_question_service_persists_failure_without_leaking_error() -> None:
    service, conversations, _ = question_service(RuntimeError("secret internal detail"))

    with pytest.raises(QuestionProcessingError, match="processing failed"):
        await service.answer("What's the 10-year yield?")

    assert conversations.records[0].status == "failed"
    assert conversations.records[0].answer is None
    assert conversations.records[0].error_code == "RuntimeError"


@pytest.mark.asyncio
async def test_question_service_preserves_agent_error_when_failure_audit_fails() -> None:
    service, conversations, _ = question_service(
        RuntimeError("original model failure"),
        completion_error=RuntimeError("audit database failure"),
    )

    with pytest.raises(QuestionProcessingError) as captured:
        await service.answer("What's the 10-year yield?")

    assert isinstance(captured.value.__cause__, RuntimeError)
    assert str(captured.value.__cause__) == "original model failure"
    assert conversations.records == []


@pytest.mark.asyncio
async def test_question_service_deadline_cancels_agent_and_records_timeout() -> None:
    conversations = FakeConversations()
    timer = iter([10.0, 10.125])
    service = QuestionService(
        agent=cast(AnsweringAgent, NeverCompletingAgent()),
        conversations=cast(ConversationStore, conversations),
        clock=lambda: datetime(2026, 8, 12, 21, 0, tzinfo=UTC),
        timer=lambda: next(timer),
        id_factory=lambda: UUID("00000000-0000-0000-0000-000000000010"),
        deadline_seconds=0.001,
    )

    with pytest.raises(QuestionProcessingError) as captured:
        await service.answer("What's the 10-year yield?")

    assert isinstance(captured.value.__cause__, TimeoutError)
    assert conversations.records[0].error_code == "TimeoutError"


@pytest.mark.asyncio
async def test_question_service_exposes_fifth_failed_attempt_rollover() -> None:
    service, conversations, _ = question_service(
        RuntimeError("model timeout"),
        turn_number=5,
    )

    with pytest.raises(QuestionProcessingError) as captured:
        await service.answer("Fifth question", session_id=SESSION_ID)

    assert captured.value.completion is not None
    assert captured.value.completion.status == "closed"
    assert captured.value.completion.next_session_id == NEXT_SESSION_ID
    assert conversations.records[0].status == "failed"


@pytest.mark.asyncio
async def test_question_service_closes_fifth_turn_and_returns_successor() -> None:
    service, _, _ = question_service(
        AgentResult(answer="Answer", status="answered", tool_calls=[]),
        turn_number=5,
        restarted=True,
    )

    response = await service.answer("Fifth question", session_id=SESSION_ID)

    assert response.session_status == "closed"
    assert response.next_session_id == NEXT_SESSION_ID
    assert response.session_restarted is True
