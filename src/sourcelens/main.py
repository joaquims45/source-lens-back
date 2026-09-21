from collections.abc import Awaitable, Callable
from time import perf_counter
from uuid import uuid4

import structlog
from fastapi import FastAPI, Request, Response

from sourcelens.api.chat import router as chat_router
from sourcelens.api.errors import install_errors
from sourcelens.api.repositories import router as repositories_router
from sourcelens.api.search import router as search_router

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.JSONRenderer(),
    ]
)
logger = structlog.get_logger()


def create_app() -> FastAPI:
    app = FastAPI(title="SourceLens API", version="0.1.0")
    install_errors(app)
    app.include_router(repositories_router)
    app.include_router(search_router)
    app.include_router(chat_router)

    @app.middleware("http")
    async def request_context(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.request_id = str(uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(request_id=request.state.request_id)
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
