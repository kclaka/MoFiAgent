from datetime import UTC, date, datetime
from typing import cast
from uuid import UUID

import pytest

from mofiagent.agent.models import AgentResult, ToolCallRecord
from mofiagent.application import (
    AnsweringAgent,
    InteractionWriter,
    QuestionProcessingError,
    QuestionService,
)
from mofiagent.rates.repository import InteractionRecord


class FakeAgent:
    def __init__(self, result: AgentResult | Exception) -> None:
        self._result = result

    @property
    def model_name(self) -> str:
        return "fake-model"

    async def answer(self, question: str) -> AgentResult:
        assert question
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class FakeInteractions:
    def __init__(self) -> None:
        self.records: list[InteractionRecord] = []

    async def save(self, record: InteractionRecord) -> None:
        self.records.append(record)


def question_service(
    result: AgentResult | Exception,
) -> tuple[QuestionService, FakeInteractions]:
    interactions = FakeInteractions()
    timer = iter([10.0, 10.125])
    service = QuestionService(
        agent=cast(AnsweringAgent, FakeAgent(result)),
        interactions=cast(InteractionWriter, interactions),
        clock=lambda: datetime(2026, 8, 12, 21, 0, tzinfo=UTC),
        timer=lambda: next(timer),
        id_factory=lambda: UUID("00000000-0000-0000-0000-000000000010"),
    )
    return service, interactions


@pytest.mark.asyncio
async def test_question_service_persists_answer() -> None:
    service, interactions = question_service(
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

    response = await service.answer("What's the 10-year yield?")

    assert response.source is not None
    assert str(response.source.url) == "https://home.treasury.gov/example"
    assert interactions.records[0].status == "answered"
    assert interactions.records[0].latency_ms == 125


@pytest.mark.asyncio
async def test_question_service_persists_failure_without_leaking_error() -> None:
    service, interactions = question_service(RuntimeError("secret internal detail"))

    with pytest.raises(QuestionProcessingError, match="processing failed"):
        await service.answer("What's the 10-year yield?")

    assert interactions.records[0].status == "failed"
    assert interactions.records[0].answer is None
    assert interactions.records[0].error_code == "RuntimeError"
