import asyncio
from collections.abc import Sequence
from typing import Protocol

from mofiagent.agent.gateway import ModelGateway
from mofiagent.agent.models import (
    AgentExhaustedError,
    AgentResult,
    ConversationExchange,
    ModelToolCall,
    ToolCallRecord,
    ToolResult,
)
from mofiagent.agent.tools import result_dates, result_source_urls

UNSUPPORTED_ANSWER = (
    "I can answer questions about official nominal U.S. Treasury yields for supported tenors, "
    "including latest values, historical values, changes, and curve spreads."
)
UNAVAILABLE_ANSWER = (
    "I couldn't retrieve a matching official Treasury observation for that question. "
    "Try another supported tenor or date."
)


class ToolExecutor(Protocol):
    async def execute(self, call: ModelToolCall) -> ToolResult: ...


class AgentService:
    def __init__(
        self,
        *,
        gateway: ModelGateway,
        tools: ToolExecutor,
        max_rounds: int,
        max_tool_calls: int = 8,
    ) -> None:
        if max_rounds < 1:
            raise ValueError("max_rounds must be positive")
        if max_tool_calls < 1:
            raise ValueError("max_tool_calls must be positive")
        self._gateway = gateway
        self._tools = tools
        self._max_rounds = max_rounds
        self._max_tool_calls = max_tool_calls

    @property
    def model_name(self) -> str:
        return self._gateway.model_name

    async def answer(
        self,
        question: str,
        *,
        history: Sequence[ConversationExchange] = (),
    ) -> AgentResult:
        session = self._gateway.create_session(history)
        turn = await session.ask(question)
        records: list[ToolCallRecord] = []
        successful_payloads: list[dict[str, object]] = []

        for round_number in range(self._max_rounds):
            if turn.tool_calls:
                if len(records) + len(turn.tool_calls) > self._max_tool_calls:
                    raise AgentExhaustedError("model exceeded the tool-call budget")
                results = await asyncio.gather(
                    *(self._tools.execute(call) for call in turn.tool_calls)
                )
                for call, result in zip(turn.tool_calls, results, strict=True):
                    records.append(
                        ToolCallRecord(
                            name=call.name,
                            arguments=call.arguments,
                            success=result.ok,
                        )
                    )
                    if result.ok:
                        successful_payloads.append(result.payload)
                if round_number == self._max_rounds - 1:
                    raise AgentExhaustedError("model exceeded the agent-round budget")
                turn = await session.continue_with(results)
                continue

            if not successful_payloads:
                if records:
                    return AgentResult(
                        answer=UNAVAILABLE_ANSWER,
                        status="unavailable",
                        tool_calls=records,
                    )
                return AgentResult(
                    answer=UNSUPPORTED_ANSWER,
                    status="unsupported",
                    tool_calls=records,
                )

            if not turn.text:
                raise AgentExhaustedError("model returned neither text nor tool calls")

            dates = [item for payload in successful_payloads for item in result_dates(payload)]
            source_urls = {
                item for payload in successful_payloads for item in result_source_urls(payload)
            }
            return AgentResult(
                answer=turn.text,
                status="answered",
                tool_calls=records,
                data_as_of=max(dates) if dates else None,
                source_urls=sorted(source_urls),
            )
        raise AssertionError("agent loop exited without returning")
