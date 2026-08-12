import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable

from fastapi import FastAPI, HTTPException, Request, status

from mofiagent import __version__
from mofiagent.agent.gateway import VertexModelGateway
from mofiagent.agent.service import AgentService
from mofiagent.agent.tools import TOOL_SCHEMAS, RateTools
from mofiagent.api.models import HealthResponse, QuestionRequest, QuestionResponse
from mofiagent.application import QuestionProcessingError, QuestionService
from mofiagent.config import get_settings
from mofiagent.database import Database
from mofiagent.rates.repository import InteractionRepository, RateRepository

LOGGER = logging.getLogger(__name__)


async def health() -> HealthResponse:
    return HealthResponse()


@runtime_checkable
class QuestionAnswerer(Protocol):
    async def answer(self, question: str) -> QuestionResponse: ...


def _question_service(request: Request) -> QuestionAnswerer:
    service = getattr(request.app.state, "question_service", None)
    if not isinstance(service, QuestionAnswerer):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service is not ready",
        )
    return service


async def answer_question(request: Request, body: QuestionRequest) -> QuestionResponse:
    try:
        return await _question_service(request).answer(body.question)
    except QuestionProcessingError as error:
        LOGGER.exception("question processing failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="question could not be answered",
        ) from error


def create_app(
    *,
    initialize_dependencies: bool = True,
    question_service: QuestionAnswerer | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        if not initialize_dependencies:
            if question_service is not None:
                app.state.question_service = question_service
            yield
            return

        settings = get_settings()
        settings.validate_for_service()
        assert settings.database_dsn is not None
        assert settings.google_cloud_project is not None
        assert settings.vertex_model is not None
        database = Database(
            settings.database_dsn.get_secret_value(),
            min_size=settings.db_pool_min_size,
            max_size=settings.db_pool_max_size,
            timeout_seconds=settings.db_pool_timeout_seconds,
            connect_timeout_seconds=settings.db_connect_timeout_seconds,
            startup_timeout_seconds=settings.db_startup_timeout_seconds,
        )
        await database.open()
        repository = RateRepository(database)
        gateway = VertexModelGateway(
            project=settings.google_cloud_project,
            location=settings.google_cloud_location,
            model_name=settings.vertex_model,
            tools=TOOL_SCHEMAS,
            timeout_seconds=settings.request_timeout_seconds,
        )
        app.state.question_service = QuestionService(
            agent=AgentService(
                gateway=gateway,
                tools=RateTools(repository),
                max_rounds=settings.max_agent_rounds,
            ),
            interactions=InteractionRepository(database),
        )
        try:
            yield
        finally:
            await database.close()

    app = FastAPI(
        title="MoFiAgent",
        summary="Agentic U.S. Treasury rate service",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    if question_service is not None:
        app.state.question_service = question_service

    app.add_api_route(
        "/health",
        health,
        methods=["GET"],
        response_model=HealthResponse,
        tags=["operations"],
    )
    app.add_api_route(
        "/v1/questions",
        answer_question,
        methods=["POST"],
        response_model=QuestionResponse,
        status_code=status.HTTP_200_OK,
        tags=["questions"],
    )

    return app


app = create_app()
