"""Centralized HTTP exception mapping for application errors.

Routers raise typed domain errors; this module translates them into HTTP
responses so no router has to reverse-engineer status codes from message text.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    AlreadySentError,
    AppError,
    ConfigurationError,
    ConflictError,
    DataIntegrityError,
    NotFoundError,
    OutreachNotReadyError,
    SourceError,
    ValidationError,
)


def register_exception_handlers(app: FastAPI) -> None:
    """Register handlers mapping typed application errors to HTTP responses."""

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @app.exception_handler(ValidationError)
    async def _validation(_: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ConflictError)
    async def _conflict(_: Request, exc: ConflictError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @app.exception_handler(AlreadySentError)
    async def _already_sent(_: Request, exc: AlreadySentError) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc), "attempt_id": exc.attempt_id})

    @app.exception_handler(OutreachNotReadyError)
    async def _not_ready(_: Request, exc: OutreachNotReadyError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.as_detail()})

    @app.exception_handler(SourceError)
    async def _source_error(_: Request, exc: SourceError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(ConfigurationError)
    async def _config_error(_: Request, exc: ConfigurationError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(DataIntegrityError)
    async def _data_integrity(_: Request, exc: DataIntegrityError) -> JSONResponse:
        return JSONResponse(status_code=500, content={"detail": str(exc)})

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})
