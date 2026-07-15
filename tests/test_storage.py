from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from incident_triage.analyzer import analyze_log
from incident_triage.similarity import fingerprint_for_finding, stable_attributes_from_finding
from incident_triage.storage import (
    DB_SCHEMA_VERSION,
    StorageError,
    UnsupportedSchemaVersionError,
    add_incident,
    connect,
    find_candidates_by_type,
    find_similar_incidents,
    get_incident,
    initialize_database,
    list_history,
    schema_version,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def first_finding(name: str) -> dict[str, object]:
    return analyze_log(FIXTURES / name)["findings"][0]  # type: ignore[index,return-value]


def test_initialize_database_is_idempotent_and_sets_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"

    initialize_database(db_path)
    initialize_database(db_path)

    assert schema_version(db_path) == DB_SCHEMA_VERSION


def test_connection_pragmas_and_persistence(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    initialize_database(db_path)

    with connect(db_path) as connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        assert connection.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"

    stored = add_incident(db_path, first_finding("api_timeout.log"), "fixtures/api_timeout.log", "resolved")
    reopened = get_incident(db_path, stored["id"])
    assert reopened is not None
    assert reopened["resolution"] == "resolved"


def test_locked_database_returns_controlled_storage_error(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    initialize_database(db_path)
    locker = sqlite3.connect(db_path)
    locker.execute("BEGIN EXCLUSIVE")

    try:
        with pytest.raises(StorageError):
            add_incident(db_path, first_finding("api_timeout.log"), "fixtures/api_timeout.log", "resolved")
    finally:
        locker.rollback()
        locker.close()


def test_add_and_get_incident_preserves_unicode_and_json(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    finding = first_finding("api_timeout.log")

    stored = add_incident(db_path, finding, "fixtures/api_timeout.log", "Provider's latency fixed zażółć")
    loaded = get_incident(db_path, stored["id"])

    assert loaded is not None
    assert loaded["resolution"] == "Provider's latency fixed zażółć"
    assert loaded["evidence"] == finding["evidence"]
    assert loaded["stable_attributes"] == {"endpoint": "https://payments.example.test/v1/charge"}


def test_stable_attributes_use_allowlist_only() -> None:
    finding = first_finding("api_timeout.log")

    assert stable_attributes_from_finding(finding) == {"endpoint": "https://payments.example.test/v1/charge"}


def test_fingerprint_is_deterministic_and_ignores_request_specific_fields() -> None:
    base = first_finding("api_timeout.log")
    variant = {
        **base,
        "evidence": [
            {
                "line_number": 99,
                "text": "2027-01-01T00:00:00Z ERROR request_id=changed upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge",
            }
        ],
    }

    assert fingerprint_for_finding(base) == fingerprint_for_finding(base)
    assert fingerprint_for_finding(base) == fingerprint_for_finding(variant)


def test_candidates_are_filtered_by_incident_type(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    add_incident(db_path, first_finding("api_timeout.log"), "fixtures/api_timeout.log", "timeout fixed")
    add_incident(
        db_path,
        first_finding("database_connection_error.log"),
        "fixtures/database_connection_error.log",
        "database fixed",
    )

    candidates = find_candidates_by_type(db_path, "external_api_timeout")

    assert [candidate["incident_type"] for candidate in candidates] == ["external_api_timeout"]


def test_similarity_ranking_sorting_and_limit(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    finding = first_finding("api_timeout.log")
    weak = {
        **finding,
        "evidence": [
            {
                "line_number": 1,
                "text": "2026-07-15T09:14:05Z ERROR upstream payment API timed out after 3000ms endpoint=https://other.example.test/v1/charge",
            }
        ],
    }
    same_newer = add_incident(
        db_path,
        finding,
        "fixtures/api_timeout.log",
        "same newer",
        created_at="2026-07-15T12:00:00Z",
    )
    weak_old = add_incident(db_path, weak, "fixtures/api_timeout.log", "weak old", created_at="2026-07-15T11:00:00Z")
    same_older = add_incident(
        db_path,
        finding,
        "fixtures/api_timeout.log",
        "same older",
        created_at="2026-07-15T10:00:00Z",
    )

    matches = find_similar_incidents(db_path, finding, limit=2)

    assert [match["id"] for match in matches] == [same_newer["id"], same_older["id"]]
    assert matches[0]["similarity_score"] == 0.7
    assert matches[0]["match_reasons"] == [
        "incident_type=external_api_timeout",
        "endpoint=https://payments.example.test/v1/charge",
    ]
    assert weak_old["id"] not in [match["id"] for match in matches]


def test_same_score_sorting_uses_newer_created_at_then_higher_id_for_history_list(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    finding = first_finding("api_timeout.log")
    first = add_incident(db_path, finding, "fixtures/api_timeout.log", "first", created_at="2026-07-15T10:00:00Z")
    second = add_incident(db_path, finding, "fixtures/api_timeout.log", "second", created_at="2026-07-15T10:00:00Z")

    assert [incident["id"] for incident in list_history(db_path)] == [second["id"], first["id"]]


def test_unsupported_schema_version_is_reported(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(UnsupportedSchemaVersionError):
        initialize_database(db_path)
