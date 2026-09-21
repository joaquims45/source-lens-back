from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        self.code, self.message, self.status = code, message, status
        super().__init__(message)


def install_errors(app: FastAPI) -> None:
    def problem(request: Request, status: int, code: str, detail: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            content={
                "type": f"urn:sourcelens:{code}",
                "title": code,
                "status": status,
                "detail": detail,
                "request_id": request.state.request_id,
            },
        )

    @app.exception_handler(DomainError)
    async def domain_error(request: Request, exc: DomainError) -> JSONResponse:
        return problem(request, exc.status, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        return problem(request, 422, "validation_error", "Invalid request parameters")

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        return problem(request, exc.status_code, "http_error", str(exc.detail))

    @app.exception_handler(Exception)
    async def unexpected(request: Request, exc: Exception) -> JSONResponse:
        return problem(request, 500, "internal_error", "Unexpected server error")
