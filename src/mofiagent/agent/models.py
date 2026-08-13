from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ModelToolCall(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1, max_length=100)
    arguments: dict[str, Any]


class ModelTurn(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str | None = None
    tool_calls: list[ModelToolCall] = Field(default_factory=list[ModelToolCall])


class ToolResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    ok: bool
    payload: dict[str, Any]


class ToolCallRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    arguments: dict[str, Any]
    success: bool


class ConversationExchange(BaseModel):
    model_config = ConfigDict(frozen=True)

    question: str
    answer: str


class AgentResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str
    status: Literal["answered", "unsupported"]
    tool_calls: list[ToolCallRecord]
    data_as_of: date | None = None
    source_urls: list[str] = Field(default_factory=list)


class AgentExhaustedError(RuntimeError):
    """The model did not produce a final response within the configured budget."""
