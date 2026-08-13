from uuid import UUID

import httpx
import pytest

from mofiagent.api.app import create_app
from mofiagent.api.models import QuestionResponse
from mofiagent.application import QuestionProcessingError
from mofiagent.rates.repository import (
    SessionBusyError,
    SessionCompletion,
    SessionNotFoundError,
)


class StubQuestionService:
    def __init__(self, *, error: Exception | None = None) -> None:
        self._error = error

    async def answer(
        self,
        question: str,
        *,
        session_id: UUID | None = None,
    ) -> QuestionResponse:
        if self._error is not None:
            raise self._error
        return QuestionResponse.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000001",
                "session_id": session_id or "00000000-0000-0000-0000-000000000002",
                "turn_number": 1,
                "session_status": "active",
                "question": question,
                "answer": "The 10-year yield was 4.68% on August 12, 2026.",
                "data_as_of": "2026-08-12",
                "source": {
                    "name": "U.S. Department of the Treasury",
                    "url": "https://home.treasury.gov/example",
                },
            }
        )


@pytest.mark.asyncio
async def test_health() -> None:
    app = create_app(initialize_dependencies=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.asyncio
async def test_openapi_is_not_publicly_exposed() -> None:
    app = create_app(initialize_dependencies=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        docs = await client.get("/docs")
        openapi = await client.get("/openapi.json")

    assert docs.status_code == 404
    assert openapi.status_code == 404


@pytest.mark.asyncio
async def test_questions_require_initialized_service() -> None:
    app = create_app(initialize_dependencies=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/v1/questions", json={"question": "What is the 10y?"})

    assert response.status_code == 503
    assert response.json() == {"detail": "service is not ready"}


@pytest.mark.asyncio
async def test_question_validation_rejects_extra_and_oversized_input() -> None:
    app = create_app(initialize_dependencies=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "x" * 501, "unexpected": True},
        )

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_questions_return_grounded_service_response() -> None:
    app = create_app(
        initialize_dependencies=False,
        question_service=StubQuestionService(),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={
                "question": "What is the 10-year yield?",
                "session_id": "00000000-0000-0000-0000-000000000099",
            },
        )

    assert response.status_code == 200
    assert response.json()["data_as_of"] == "2026-08-12"
    assert response.json()["session_id"] == "00000000-0000-0000-0000-000000000099"


@pytest.mark.asyncio
async def test_questions_hide_processing_failures() -> None:
    app = create_app(
        initialize_dependencies=False,
        question_service=StubQuestionService(error=QuestionProcessingError("internal detail")),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "What is the 10-year yield?"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "question could not be answered"}


@pytest.mark.asyncio
async def test_questions_return_session_metadata_for_failed_fifth_attempt() -> None:
    error = QuestionProcessingError(
        "internal detail",
        completion=SessionCompletion(
            session_id=UUID("00000000-0000-0000-0000-000000000010"),
            turn_number=5,
            status="closed",
            next_session_id=UUID("00000000-0000-0000-0000-000000000011"),
        ),
    )
    app = create_app(
        initialize_dependencies=False,
        question_service=StubQuestionService(error=error),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "What is the 10-year yield?"},
        )

    assert response.status_code == 503
    assert response.headers["x-mofi-session-status"] == "closed"
    assert response.headers["x-mofi-turn-number"] == "5"
    assert response.headers["x-mofi-next-session-id"] == ("00000000-0000-0000-0000-000000000011")
    assert response.json() == {
        "detail": "question could not be answered",
        "session_id": "00000000-0000-0000-0000-000000000010",
        "turn_number": 5,
        "session_status": "closed",
        "next_session_id": "00000000-0000-0000-0000-000000000011",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "expected_status", "expected_detail"),
    [
        (SessionNotFoundError(), 404, "session was not found"),
        (SessionBusyError(), 409, "session is already processing another question"),
    ],
)
async def test_questions_map_session_errors(
    error: Exception,
    expected_status: int,
    expected_detail: str,
) -> None:
    app = create_app(
        initialize_dependencies=False,
        question_service=StubQuestionService(error=error),
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "What is the 10-year yield?"},
        )

    assert response.status_code == expected_status
    assert response.json() == {"detail": expected_detail}
