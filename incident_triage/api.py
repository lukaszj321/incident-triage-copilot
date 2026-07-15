from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any, NoReturn, cast

from fastapi import FastAPI, Query, Request, status
from fastapi import Path as ApiPath
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator

from incident_triage import __version__
from incident_triage.models import LogSource
from incident_triage.service import (
    MAX_CONTENT_LENGTH,
    MAX_SOURCES,
    MAX_TOTAL_CONTENT_LENGTH,
    AmbiguousIncidentSelectionError,
    IncidentTypeNotFoundError,
    NoIncidentDetectedError,
    ServiceError,
    analyze_log_text,
    analyze_sources,
    get_historical_incident,
    list_incident_history,
    public_stored_incident,
    store_resolved_incident_from_text,
)
from incident_triage.service import (
    normalize_source_name as normalize_service_source_name,
)
from incident_triage.storage import StorageError, UnsupportedSchemaVersionError, initialize_database, readiness_check
from incident_triage.versions import API_VERSION, REPORT_SCHEMA_VERSION

SERVICE_NAME = "incident-triage-copilot"
REQUEST_ID_HEADER = "X-Request-ID"
REQUEST_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
request_id_context: ContextVar[str | None] = ContextVar("request_id", default=None)
api_logger = logging.getLogger("incident_triage.api")
if not api_logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    api_logger.addHandler(handler)
api_logger.setLevel(logging.INFO)
api_logger.propagate = False


class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


STANDARD_ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    422: {"model": ErrorResponse},
    500: {"model": ErrorResponse},
    503: {"model": ErrorResponse},
}


class AnalyzeRequest(BaseModel):
    source_name: str
    content: str = Field(max_length=MAX_CONTENT_LENGTH)
    similar_limit: int = Field(default=3, ge=1, le=20)

    @field_validator("source_name")
    @classmethod
    def validate_source_name(cls, value: str) -> str:
        return pydantic_normalize_source_name(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class HistoryCreateRequest(AnalyzeRequest):
    resolution: str
    incident_type: str | None = None

    @field_validator("resolution")
    @classmethod
    def validate_resolution(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("resolution must not be empty")
        return value


class LogSourceRequest(BaseModel):
    source_name: str
    content: str = Field(max_length=MAX_CONTENT_LENGTH)

    @field_validator("source_name")
    @classmethod
    def validate_source_name(cls, value: str) -> str:
        return pydantic_normalize_source_name(value)

    @field_validator("content")
    @classmethod
    def validate_content(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("content must not be empty")
        return value


class AnalyzeBundleRequest(BaseModel):
    sources: list[LogSourceRequest] = Field(min_length=1, max_length=MAX_SOURCES)
    similar_limit: int = Field(default=3, ge=1, le=20)

    @field_validator("sources")
    @classmethod
    def validate_bundle(cls, value: list[LogSourceRequest]) -> list[LogSourceRequest]:
        total_length = sum(len(source.content) for source in value)
        if total_length > MAX_TOTAL_CONTENT_LENGTH:
            raise ValueError(f"bundle content exceeds {MAX_TOTAL_CONTENT_LENGTH} characters")
        names = [source.source_name for source in value]
        if len(names) != len(set(names)):
            raise ValueError("source_name values must be unique")
        return value


def create_app(db_path: Path | None = None) -> FastAPI:
    configured_db = db_path if db_path is not None else configured_db_from_environment()
    app = FastAPI(title="Incident Triage Copilot API", version=__version__, lifespan=lifespan)
    app.state.db_path = configured_db

    @app.middleware("http")
    async def request_id_and_logging_middleware(request: Request, call_next: Any) -> Any:
        request_id = accepted_request_id(request.headers.get(REQUEST_ID_HEADER))
        token = request_id_context.set(request_id)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 3)
            api_logger.info(
                json.dumps(
                    {
                        "event": "http_request_completed",
                        "request_id": request_id,
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                        "duration_ms": duration_ms,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            request_id_context.reset(token)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            status_code=422,
            code="invalid_request",
            message="Invalid request.",
            details=sanitize_validation_errors(cast(list[dict[str, Any]], exc.errors())),
        )

    @app.exception_handler(ApiError)
    async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, exc.details)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": SERVICE_NAME,
            "api_version": API_VERSION,
            "service_version": __version__,
            "history_storage": "configured" if app.state.db_path is not None else "disabled",
        }

    @app.get("/ready", response_model=None)
    def ready() -> JSONResponse:
        if app.state.db_path is None:
            return JSONResponse(content={"status": "ready", "history_storage": "disabled"})
        if readiness_check(app.state.db_path):
            return JSONResponse(content={"status": "ready", "history_storage": "available"})
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "history_storage": "unavailable"},
        )

    @app.post(
        "/v1/analyze",
        responses=STANDARD_ERROR_RESPONSES,
    )
    def analyze(request: AnalyzeRequest) -> dict[str, Any]:
        try:
            return analyze_log_text(
                request.content,
                source_name=request.source_name,
                db_path=app.state.db_path,
                similar_limit=request.similar_limit,
            )
        except StorageError as exc:
            return raise_storage_error(exc)

    @app.post(
        "/v1/analyze-bundle",
        responses=STANDARD_ERROR_RESPONSES,
    )
    def analyze_bundle(request: AnalyzeBundleRequest) -> dict[str, Any]:
        try:
            return analyze_sources(
                [LogSource(source.source_name, source.content) for source in request.sources],
                db_path=app.state.db_path,
                similar_limit=request.similar_limit,
            )
        except ServiceError as exc:
            return raise_service_error(exc)
        except StorageError as exc:
            return raise_storage_error(exc)

    @app.post(
        "/v1/history",
        status_code=status.HTTP_201_CREATED,
        responses=STANDARD_ERROR_RESPONSES,
    )
    def add_history(request: HistoryCreateRequest) -> dict[str, Any]:
        db = require_history_storage(app)
        try:
            stored = store_resolved_incident_from_text(
                request.content,
                source_name=request.source_name,
                db_path=db,
                resolution=request.resolution,
                incident_type=request.incident_type,
            )
        except ServiceError as exc:
            return raise_service_error(exc)
        except StorageError as exc:
            return raise_storage_error(exc)

        return {"schema_version": REPORT_SCHEMA_VERSION, "stored_incident": public_stored_incident(stored)}

    @app.get(
        "/v1/history",
        responses=STANDARD_ERROR_RESPONSES,
    )
    def list_history(
        limit: int = Query(default=50, ge=1, le=200),
        incident_type: str | None = Query(default=None),
    ) -> dict[str, Any]:
        db = require_history_storage(app)
        try:
            return list_incident_history(db, limit=limit, incident_type=incident_type)
        except StorageError as exc:
            return raise_storage_error(exc)

    @app.get(
        "/v1/history/{incident_id}",
        responses={
            404: {"model": ErrorResponse},
            **STANDARD_ERROR_RESPONSES,
        },
    )
    def get_history_record(incident_id: int = ApiPath(ge=1)) -> dict[str, Any]:
        db = require_history_storage(app)
        try:
            incident = get_historical_incident(db, incident_id)
        except StorageError as exc:
            return raise_storage_error(exc)
        if incident is None:
            return raise_http_error(
                status_code=status.HTTP_404_NOT_FOUND,
                code="history_record_not_found",
                message="History record was not found.",
            )
        return incident

    return app


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    db_path = app.state.db_path
    if db_path is not None:
        initialize_database(db_path)
    yield


def configured_db_from_environment() -> Path | None:
    value = os.environ.get("INCIDENT_TRIAGE_DB")
    if not value:
        return None
    return Path(value)


def require_history_storage(app: FastAPI) -> Path:
    db_path = app.state.db_path
    if db_path is None:
        raise_http_error(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            code="history_storage_disabled",
            message="History storage is not configured.",
        )
    return db_path


def pydantic_normalize_source_name(value: str) -> str:
    try:
        return normalize_service_source_name(value)
    except ServiceError as exc:
        raise ValueError(str(exc)) from exc


def raise_service_error(exc: ServiceError) -> NoReturn:
    if isinstance(exc, NoIncidentDetectedError):
        raise_http_error(422, exc.code, "No incident was detected.")
    if isinstance(exc, AmbiguousIncidentSelectionError):
        raise_http_error(422, exc.code, str(exc))
    if isinstance(exc, IncidentTypeNotFoundError):
        raise_http_error(422, exc.code, str(exc))
    raise_http_error(422, "invalid_request", str(exc))


def raise_storage_error(exc: StorageError) -> NoReturn:
    if isinstance(exc, UnsupportedSchemaVersionError):
        raise_http_error(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "unsupported_schema_version",
            "History storage schema version is not supported.",
        )
    raise_http_error(
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "storage_error",
        "History storage is unavailable.",
    )


def raise_http_error(status_code: int, code: str, message: str, details: Any = None) -> NoReturn:
    raise ApiError(status_code=status_code, code=code, message=message, details=details)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


def error_response(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": code,
                "message": message,
                "details": details,
                "request_id": request_id_context.get(),
            }
        },
    )


def accepted_request_id(value: str | None) -> str:
    if value and REQUEST_ID_RE.fullmatch(value):
        return value
    return str(uuid.uuid4())


def sanitize_validation_errors(errors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sanitized: list[dict[str, Any]] = []
    for error in errors:
        cleaned = dict(error)
        if "ctx" in cleaned:
            cleaned["ctx"] = {key: str(value) for key, value in cleaned["ctx"].items()}
        sanitized.append(cleaned)
    return sanitized


app = create_app()
