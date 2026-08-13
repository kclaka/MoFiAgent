from collections.abc import Sequence
from typing import Protocol, cast

from google import genai
from google.genai import types

from mofiagent.agent.models import ConversationExchange, ModelToolCall, ModelTurn, ToolResult

SYSTEM_INSTRUCTION = """You are MoFiAgent, a concise U.S. Treasury yield assistant.

Rules:
- Use the provided tools for every factual or numerical rate answer.
- Never answer a rate from memory.
- Resolve follow-up references such as "that yield" from the supplied conversation history.
- The tools return percentages, dates, basis-point changes, and authoritative source URLs.
- Preserve every number exactly. Do not calculate or interpolate values yourself.
- If a date has no observation, explain that the tool returned the latest prior business-day value.
- If the question is outside supported U.S. nominal Treasury yields, say so briefly.
- Keep the final answer to two or three plain-language sentences and mention the data date.
- Do not provide investment advice.
"""


class ModelSession(Protocol):
    async def ask(self, question: str) -> ModelTurn: ...

    async def continue_with(self, results: Sequence[ToolResult]) -> ModelTurn: ...


class ModelGateway(Protocol):
    @property
    def model_name(self) -> str: ...

    def create_session(self, history: Sequence[ConversationExchange]) -> ModelSession: ...


class AsyncChat(Protocol):
    async def send_message(
        self, message: str | list[types.Part]
    ) -> types.GenerateContentResponse: ...


class VertexModelSession:
    def __init__(self, chat: AsyncChat) -> None:
        self._chat = chat

    async def ask(self, question: str) -> ModelTurn:
        response = await self._chat.send_message(question)
        return model_turn_from_response(response)

    async def continue_with(self, results: Sequence[ToolResult]) -> ModelTurn:
        parts = [
            types.Part.from_function_response(
                name=result.name,
                response={"ok": result.ok, **result.payload},
            )
            for result in results
        ]
        response = await self._chat.send_message(parts)
        return model_turn_from_response(response)


def chat_history_from(
    history: Sequence[ConversationExchange],
) -> list[types.ContentOrDict]:
    return [
        content
        for exchange in history
        for content in (
            types.Content(role="user", parts=[types.Part(text=exchange.question)]),
            types.Content(role="model", parts=[types.Part(text=exchange.answer)]),
        )
    ]


class VertexModelGateway:
    def __init__(
        self,
        *,
        project: str,
        location: str,
        model_name: str,
        tools: Sequence[types.FunctionDeclaration],
        timeout_seconds: float,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self._model_name = model_name
        self._client = genai.Client(
            enterprise=True,
            project=project,
            location=location,
            http_options=types.HttpOptions(
                api_version="v1",
                timeout=round(timeout_seconds * 1000),
            ),
        )
        self._config = types.GenerateContentConfig(
            system_instruction=SYSTEM_INSTRUCTION,
            max_output_tokens=512,
            tools=[types.Tool(function_declarations=list(tools))],
            tool_config=types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.AUTO
                )
            ),
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            thinking_config=types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL),
        )

    @property
    def model_name(self) -> str:
        return self._model_name

    def create_session(self, history: Sequence[ConversationExchange]) -> ModelSession:
        chat = self._client.aio.chats.create(
            model=self._model_name,
            config=self._config,
            history=chat_history_from(history),
        )
        return VertexModelSession(cast(AsyncChat, chat))


def model_turn_from_response(response: types.GenerateContentResponse) -> ModelTurn:
    calls: list[ModelToolCall] = []
    text_parts: list[str] = []
    if response.candidates:
        content = response.candidates[0].content
        if content and content.parts:
            for part in content.parts:
                if part.text:
                    text_parts.append(part.text)
                call = part.function_call
                if call and call.name:
                    calls.append(
                        ModelToolCall(
                            name=call.name,
                            arguments=dict(call.args or {}),
                        )
                    )

    text = "\n".join(text_parts)
    return ModelTurn(text=text.strip() if text else None, tool_calls=calls)
