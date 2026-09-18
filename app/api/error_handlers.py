"""Centralized HTTP exception mapping for application errors.

Routers raise typed domain errors; this module translates them into HTTP
responses so no router has to reverse-engineer status codes from message text.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.domain.errors import (
    AppError,
    ConflictError,
    NotFoundError,
    OutreachNotReadyError,
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

    @app.exception_handler(OutreachNotReadyError)
    async def _not_ready(_: Request, exc: OutreachNotReadyError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": exc.as_detail()})

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @app.exception_handler(ValueError)
    async def _value_error(_: Request, exc: ValueError) -> JSONResponse:
        # Fallback for legacy ValueError raises not yet migrated to typed errors.
        return JSONResponse(status_code=400, content={"detail": str(exc)})
