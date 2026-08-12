from collections.abc import Sequence
from typing import cast

import pytest
from google.genai import types

from mofiagent.agent.gateway import AsyncChat, VertexModelSession, model_turn_from_response
from mofiagent.agent.models import ToolResult


def response_with(*parts: types.Part) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(role="model", parts=list(parts)),
            )
        ]
    )


class FakeChat:
    def __init__(self, responses: Sequence[types.GenerateContentResponse]) -> None:
        self._responses = iter(responses)
        self.messages: list[str | list[types.Part]] = []

    async def send_message(self, message: str | list[types.Part]) -> types.GenerateContentResponse:
        self.messages.append(message)
        return next(self._responses)


def test_model_turn_extracts_text_and_function_calls() -> None:
    response = response_with(
        types.Part(
            function_call=types.FunctionCall(
                name="get_latest_rate",
                args={"tenor": "10y"},
            )
        )
    )

    turn = model_turn_from_response(response)

    assert turn.text is None
    assert turn.tool_calls[0].name == "get_latest_rate"
    assert turn.tool_calls[0].arguments == {"tenor": "10y"}
    assert model_turn_from_response(types.GenerateContentResponse()).tool_calls == []


@pytest.mark.asyncio
async def test_vertex_session_preserves_call_and_response_sequence() -> None:
    chat = FakeChat(
        [
            response_with(
                types.Part(
                    function_call=types.FunctionCall(name="get_latest_rate", args={"tenor": "10y"})
                )
            ),
            response_with(types.Part(text="The yield was 4.68%.")),
        ]
    )
    session = VertexModelSession(cast(AsyncChat, chat))

    first = await session.ask("What is the 10-year yield?")
    second = await session.continue_with(
        [
            ToolResult(
                name="get_latest_rate",
                ok=True,
                payload={"rate_percent": "4.68"},
            )
        ]
    )

    assert first.tool_calls[0].name == "get_latest_rate"
    assert second.text == "The yield was 4.68%."
    assert isinstance(chat.messages[1], list)
