from collections.abc import Sequence
from datetime import date
from typing import cast

import pytest

from mofiagent.agent.gateway import ModelGateway, ModelSession
from mofiagent.agent.models import (
    AgentExhaustedError,
    ConversationExchange,
    ModelToolCall,
    ModelTurn,
    ToolResult,
)
from mofiagent.agent.service import AgentService, ToolExecutor


class FakeSession:
    def __init__(self, turns: Sequence[ModelTurn]) -> None:
        self._turns = iter(turns)
        self.results: list[ToolResult] = []

    async def ask(self, question: str) -> ModelTurn:
        assert question
        return next(self._turns)

    async def continue_with(self, results: Sequence[ToolResult]) -> ModelTurn:
        self.results.extend(results)
        return next(self._turns)


class FakeGateway:
    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.history: Sequence[ConversationExchange] = ()

    @property
    def model_name(self) -> str:
        return "fake-model"

    def create_session(self, history: Sequence[ConversationExchange]) -> ModelSession:
        self.history = history
        return self.session


class FakeTools:
    async def execute(self, call: ModelToolCall) -> ToolResult:
        assert call.name == "get_latest_rate"
        return ToolResult(
            name=call.name,
            ok=True,
            payload={
                "observed_at": "2026-08-12",
                "rate_percent": "4.6800",
                "source_url": "https://home.treasury.gov/example",
            },
        )


def service(turns: Sequence[ModelTurn], *, max_rounds: int = 4) -> AgentService:
    gateway = cast(ModelGateway, FakeGateway(FakeSession(turns)))
    tools = cast(ToolExecutor, FakeTools())
    return AgentService(gateway=gateway, tools=tools, max_rounds=max_rounds)


@pytest.mark.asyncio
async def test_agent_executes_tool_before_answering() -> None:
    agent = service(
        [
            ModelTurn(
                tool_calls=[ModelToolCall(name="get_latest_rate", arguments={"tenor": "10y"})]
            ),
            ModelTurn(text="The 10-year Treasury yield was 4.6800% on August 12, 2026."),
        ]
    )

    result = await agent.answer("What's the 10-year Treasury yield?")

    assert result.status == "answered"
    assert result.data_as_of == date(2026, 8, 12)
    assert result.source_urls == ["https://home.treasury.gov/example"]
    assert result.tool_calls[0].success is True


@pytest.mark.asyncio
async def test_agent_rejects_ungrounded_model_text() -> None:
    result = await service([ModelTurn(text="A made-up answer")]).answer("What is SOFR?")

    assert result.status == "unsupported"
    assert "nominal U.S. Treasury" in result.answer


@pytest.mark.asyncio
async def test_agent_enforces_round_budget() -> None:
    tool_turn = ModelTurn(
        tool_calls=[ModelToolCall(name="get_latest_rate", arguments={"tenor": "10y"})]
    )
    with pytest.raises(AgentExhaustedError, match="round"):
        await service([tool_turn, tool_turn], max_rounds=1).answer("Keep calling")


def test_agent_rejects_invalid_budgets() -> None:
    gateway = cast(ModelGateway, FakeGateway(FakeSession([])))
    tools = cast(ToolExecutor, FakeTools())
    with pytest.raises(ValueError, match="max_rounds"):
        AgentService(gateway=gateway, tools=tools, max_rounds=0)
    with pytest.raises(ValueError, match="max_tool_calls"):
        AgentService(gateway=gateway, tools=tools, max_rounds=1, max_tool_calls=0)
