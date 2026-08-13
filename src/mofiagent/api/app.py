import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Protocol, runtime_checkable
from uuid import UUID

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from mofiagent import __version__
from mofiagent.agent.gateway import VertexModelGateway
from mofiagent.agent.service import AgentService
from mofiagent.agent.tools import TOOL_SCHEMAS, RateTools
from mofiagent.api.models import HealthResponse, QuestionRequest, QuestionResponse
from mofiagent.application import QuestionProcessingError, QuestionService
from mofiagent.config import get_settings
from mofiagent.database import Database
from mofiagent.rates.repository import (
    ConversationRepository,
    RateRepository,
    SessionBusyError,
    SessionLeaseLostError,
    SessionNotFoundError,
)

LOGGER = logging.getLogger(__name__)


async def health() -> HealthResponse:
    return HealthResponse()


@runtime_checkable
class QuestionAnswerer(Protocol):
    async def answer(
        self,
        question: str,
        *,
        session_id: UUID | None = None,
    ) -> QuestionResponse: ...


def _question_service(request: Request) -> QuestionAnswerer:
    service = getattr(request.app.state, "question_service", None)
    if not isinstance(service, QuestionAnswerer):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="service is not ready",
        )
    return service


async def answer_question(
    request: Request,
    body: QuestionRequest,
) -> QuestionResponse | JSONResponse:
    try:
        return await _question_service(request).answer(
            body.question,
            session_id=body.session_id,
        )
    except SessionNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="session was not found",
        ) from error
    except (SessionBusyError, SessionLeaseLostError) as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="session is already processing another question",
        ) from error
    except QuestionProcessingError as error:
        LOGGER.exception("question processing failed")
        headers = None
        content: dict[str, str | int | None] = {
            "detail": "question could not be answered",
        }
        if error.completion is not None:
            headers = {
                "X-MoFi-Session-Id": str(error.completion.session_id),
                "X-MoFi-Turn-Number": str(error.completion.turn_number),
                "X-MoFi-Session-Status": error.completion.status,
            }
            content.update(
                {
                    "session_id": str(error.completion.session_id),
                    "turn_number": error.completion.turn_number,
                    "session_status": error.completion.status,
                    "next_session_id": (
                        str(error.completion.next_session_id)
                        if error.completion.next_session_id is not None
                        else None
                    ),
                }
            )
            if error.completion.next_session_id is not None:
                headers["X-MoFi-Next-Session-Id"] = str(error.completion.next_session_id)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content=content,
            headers=headers,
        )


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
            conversations=ConversationRepository(
                database,
                lease_seconds=settings.session_lease_seconds,
            ),
            deadline_seconds=settings.question_deadline_seconds,
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
