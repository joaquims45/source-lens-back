from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from sourcelens.api.architecture import router as architecture_router
from sourcelens.api.chat import router as chat_router
from sourcelens.api.errors import install_errors
from sourcelens.api.repositories import router as repositories_router
from sourcelens.api.search import router as search_router
from sourcelens.api.tracing import router as tracing_router
from sourcelens.config import Settings, get_settings

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="SourceLens API", version="0.1.0")
    install_errors(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origin_list,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "Idempotency-Key", "X-API-Key"],
    )
    app.include_router(repositories_router)
    app.include_router(search_router)
    app.include_router(chat_router)
    app.include_router(architecture_router)
    app.include_router(tracing_router)

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = str(uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request.state.request_id)

        # Off by default (Settings.api_key is None) for the local
        # single-operator setup the Milestone 0 design describes; set
        # API_KEY before exposing the service beyond localhost.
        if (
            settings.api_key
            and request.url.path.startswith("/api/")
            and request.headers.get("x-api-key") != settings.api_key
        ):
            return JSONResponse(
                status_code=401,
                media_type="application/problem+json",
                content={
                    "type": "urn:sourcelens:unauthorized",
                    "title": "unauthorized",
                    "status": 401,
                    "detail": "Missing or invalid X-API-Key header",
                    "request_id": request.state.request_id,
                },
            )

        started = perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        logger.info(
            "http_request",
            method=request.method,
            status=response.status_code,
            latency_ms=round((perf_counter() - started) * 1000, 2),
        )
        return response

    @app.get("/health/live")
    def live() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
