from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from incident_triage.analyzer import analyze_sources as analyze_domain_sources
from incident_triage.models import LogSource
from incident_triage.storage import (
    DatabaseNotFoundError,
    add_incident,
    find_similar_incidents,
    get_incident,
    history_report,
)
from incident_triage.versions import REPORT_SCHEMA_VERSION


class ServiceError(Exception):
    code = "invalid_request"


class NoIncidentDetectedError(ServiceError):
    code = "no_incident_detected"


class AmbiguousIncidentSelectionError(ServiceError):
    code = "ambiguous_incident_selection"


class IncidentTypeNotFoundError(ServiceError):
    code = "incident_type_not_found"


MAX_SOURCES = 20
MAX_SOURCE_NAME_LENGTH = 255
MAX_CONTENT_LENGTH = 1_000_000
MAX_TOTAL_CONTENT_LENGTH = 5_000_000


def analyze_log_text(
    content: str,
    source_name: str,
    db_path: Path | None = None,
    similar_limit: int = 3,
) -> dict[str, Any]:
    return analyze_sources(
        [LogSource(source_name=source_name, content=content)],
        db_path=db_path,
        similar_limit=similar_limit,
    )


def analyze_log_file(
    log_path: Path,
    db_path: Path | None = None,
    similar_limit: int = 3,
) -> dict[str, Any]:
    content = log_path.read_text(encoding="utf-8")
    return analyze_log_text(
        content,
        source_name=log_path.as_posix(),
        db_path=db_path,
        similar_limit=similar_limit,
    )


def analyze_sources(
    sources: Sequence[LogSource],
    db_path: Path | None = None,
    similar_limit: int = 3,
) -> dict[str, Any]:
    validated_sources = validate_sources(sources)
    report = analyze_domain_sources(validated_sources)
    return attach_similar_incidents(report, db_path=db_path, similar_limit=similar_limit)


def attach_similar_incidents(
    report: dict[str, Any],
    db_path: Path | None,
    similar_limit: int,
) -> dict[str, Any]:
    if db_path is None:
        return report
    for finding in report["findings"]:
        try:
            finding["similar_incidents"] = find_similar_incidents(db_path, finding, limit=similar_limit)
        except DatabaseNotFoundError:
            finding["similar_incidents"] = []
    return report


def store_resolved_incident_from_text(
    content: str,
    source_name: str,
    db_path: Path,
    resolution: str,
    incident_type: str | None = None,
) -> dict[str, Any]:
    report = analyze_log_text(content, source_name=source_name)
    return store_resolved_finding(report, db_path=db_path, resolution=resolution, incident_type=incident_type)


def store_resolved_incident_from_file(
    log_path: Path,
    db_path: Path,
    resolution: str,
    incident_type: str | None = None,
) -> dict[str, Any]:
    report = analyze_log_file(log_path)
    return store_resolved_finding(report, db_path=db_path, resolution=resolution, incident_type=incident_type)


def store_resolved_finding(
    report: dict[str, Any],
    db_path: Path,
    resolution: str,
    incident_type: str | None = None,
) -> dict[str, Any]:
    clean_resolution = resolution.strip()
    if not clean_resolution:
        raise ServiceError("resolution must not be empty")
    finding = select_finding(report["findings"], incident_type)
    source_file = str(report["sources"][0]["source_name"])
    return add_incident(db_path, finding, source_file=source_file, resolution=clean_resolution)


def select_finding(findings: list[dict[str, Any]], incident_type: str | None) -> dict[str, Any]:
    if not findings:
        raise NoIncidentDetectedError("cannot store history: no findings detected")

    if len(findings) == 1 and incident_type is None:
        return findings[0]

    if incident_type is None:
        raise AmbiguousIncidentSelectionError("multiple findings detected; provide incident_type")

    matches = [finding for finding in findings if finding["incident_type"] == incident_type]
    if not matches:
        raise IncidentTypeNotFoundError(f"incident type not found in report: {incident_type}")
    if len(matches) > 1:
        raise AmbiguousIncidentSelectionError(f"incident type is ambiguous in report: {incident_type}")
    return matches[0]


def list_incident_history(
    db_path: Path,
    limit: int | None = None,
    incident_type: str | None = None,
) -> dict[str, Any]:
    return history_report(db_path, limit=limit, incident_type=incident_type)


def get_historical_incident(db_path: Path, incident_id: int) -> dict[str, Any] | None:
    return get_incident(db_path, incident_id)


def public_stored_incident(incident: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": incident["id"],
        "incident_type": incident["incident_type"],
        "resolution": incident["resolution"],
        "source_file": incident["source_file"],
        "created_at": incident["created_at"],
    }


def stored_incident_response(incident: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "stored_incident": public_stored_incident(incident),
    }


def validate_sources(sources: Sequence[LogSource]) -> list[LogSource]:
    source_list = list(sources)
    if not source_list:
        raise ServiceError("at least one source is required")
    if len(source_list) > MAX_SOURCES:
        raise ServiceError(f"at most {MAX_SOURCES} sources are allowed")

    seen: set[str] = set()
    validated: list[LogSource] = []
    total_length = 0

    for source in source_list:
        source_name = normalize_source_name(source.source_name)
        if source_name in seen:
            raise ServiceError(f"duplicate source_name: {source_name}")
        seen.add(source_name)

        content = source.content
        if not content.strip():
            raise ServiceError("content must not be empty")
        if len(content) > MAX_CONTENT_LENGTH:
            raise ServiceError(f"source content exceeds {MAX_CONTENT_LENGTH} characters")
        total_length += len(content)
        if total_length > MAX_TOTAL_CONTENT_LENGTH:
            raise ServiceError(f"bundle content exceeds {MAX_TOTAL_CONTENT_LENGTH} characters")

        validated.append(LogSource(source_name=source_name, content=content))

    return validated


def normalize_source_name(value: str) -> str:
    source_name = value.strip().replace("\\", "/")
    if not source_name:
        raise ServiceError("source_name must not be empty")
    if len(source_name) > MAX_SOURCE_NAME_LENGTH:
        raise ServiceError(f"source_name must be at most {MAX_SOURCE_NAME_LENGTH} characters")
    parts = [part for part in source_name.split("/") if part]
    if ".." in parts or source_name.startswith("/"):
        raise ServiceError("source_name must be a safe logical name")
    return "/".join(parts) if parts else source_name
