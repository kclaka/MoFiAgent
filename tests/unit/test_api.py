import httpx
import pytest

from mofiagent.api.app import create_app
from mofiagent.api.models import QuestionResponse
from mofiagent.application import QuestionProcessingError


class StubQuestionService:
    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail

    async def answer(self, question: str) -> QuestionResponse:
        if self._fail:
            raise QuestionProcessingError("internal detail")
        return QuestionResponse.model_validate(
            {
                "id": "00000000-0000-0000-0000-000000000001",
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
async def test_healthz() -> None:
    app = create_app(initialize_dependencies=False)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/healthz")

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
            json={"question": "What is the 10-year yield?"},
        )

    assert response.status_code == 200
    assert response.json()["data_as_of"] == "2026-08-12"


@pytest.mark.asyncio
async def test_questions_hide_processing_failures() -> None:
    app = create_app(
        initialize_dependencies=False,
        question_service=StubQuestionService(fail=True),
    )
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/v1/questions",
            json={"question": "What is the 10-year yield?"},
        )

    assert response.status_code == 503
    assert response.json() == {"detail": "question could not be answered"}
