from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, StringConstraints

QuestionText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=3, max_length=500),
]


class QuestionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: QuestionText
    session_id: UUID | None = None


class SourceReference(BaseModel):
    name: str
    url: AnyHttpUrl


class QuestionResponse(BaseModel):
    id: UUID
    session_id: UUID
    turn_number: int = Field(ge=1, le=5)
    session_status: Literal["active", "closed"]
    next_session_id: UUID | None = None
    session_restarted: bool = False
    question: str
    answer: str
    data_as_of: date | None
    source: SourceReference | None


class HistoryItem(QuestionResponse):
    created_at: datetime
    status: Literal["answered", "unsupported", "failed"]


class HistoryResponse(BaseModel):
    items: list[HistoryItem]
    next_cursor: str | None = None


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    database: Literal["available", "unavailable", "not_configured"]
    latest_observation: date | None = None
    detail: str | None = Field(default=None, max_length=200)
