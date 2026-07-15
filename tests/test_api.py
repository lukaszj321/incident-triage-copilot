from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from incident_triage.api import create_app
from incident_triage.storage import DB_SCHEMA_VERSION, StorageError, initialize_database, schema_version
from incident_triage.versions import REPORT_SCHEMA_VERSION

FIXTURES = Path(__file__).parents[1] / "fixtures"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_health_without_database() -> None:
    client = TestClient(create_app())

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "incident-triage-copilot",
        "api_version": "1",
        "service_version": "0.6.0",
        "history_storage": "disabled",
    }


def test_health_with_database_hides_path(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "incidents.db"))

    response = client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["history_storage"] == "configured"
    assert body["service_version"] == "0.6.0"
    assert str(tmp_path) not in str(body)


def test_ready_without_database() -> None:
    response = TestClient(create_app()).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "history_storage": "disabled"}


def test_ready_with_available_database(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    initialize_database(db_path)

    response = TestClient(create_app(db_path=db_path)).get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "history_storage": "available"}


def test_ready_with_missing_or_corrupt_database_hides_path(tmp_path: Path) -> None:
    missing = tmp_path / "missing.db"
    response = TestClient(create_app(db_path=missing)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "history_storage": "unavailable"}
    assert str(missing) not in response.text

    corrupt = tmp_path / "corrupt.db"
    corrupt.write_text("not sqlite", encoding="utf-8")
    response = TestClient(create_app(db_path=corrupt)).get("/ready")
    assert response.status_code == 503
    assert response.json() == {"status": "not_ready", "history_storage": "unavailable"}
    assert str(corrupt) not in response.text


def test_request_id_generated_preserved_and_in_error() -> None:
    client = TestClient(create_app())
    generated = client.get("/health")
    assert generated.headers["X-Request-ID"]

    preserved = client.get("/health", headers={"X-Request-ID": "client-123_ok.test"})
    assert preserved.headers["X-Request-ID"] == "client-123_ok.test"

    replaced = client.get("/health", headers={"X-Request-ID": "bad header!"})
    assert replaced.headers["X-Request-ID"] != "bad header!"

    error = client.post("/v1/analyze", json={"source_name": "../bad.log", "content": "x"})
    assert error.status_code == 422
    assert error.json()["error"]["request_id"] == error.headers["X-Request-ID"]


def test_structured_request_log_has_no_body_evidence_or_token(caplog: pytest.LogCaptureFixture) -> None:
    client = TestClient(create_app())

    with caplog.at_level(logging.INFO, logger="incident_triage.api"):
        response = client.post(
            "/v1/analyze",
            json={
                "source_name": "api_timeout.log",
                "content": read_fixture("api_timeout.log") + "\ntoken=secret-value",
            },
        )

    assert response.status_code == 200
    records = [json.loads(record.message) for record in caplog.records if "http_request_completed" in record.message]
    assert len(records) == 1
    record = records[0]
    assert record["event"] == "http_request_completed"
    assert record["method"] == "POST"
    assert record["path"] == "/v1/analyze"
    assert record["status_code"] == 200
    assert isinstance(record["duration_ms"], float)
    assert "payment API timed out" not in caplog.text
    assert "secret-value" not in caplog.text


def test_create_app_uses_explicit_db_path_before_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("INCIDENT_TRIAGE_DB", str(tmp_path / "env.db"))
    explicit = tmp_path / "explicit.db"

    client = TestClient(create_app(db_path=explicit))

    assert client.app.state.db_path == explicit


def test_create_app_reads_database_path_from_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_db = tmp_path / "env.db"
    monkeypatch.setenv("INCIDENT_TRIAGE_DB", str(env_db))

    client = TestClient(create_app())

    assert client.app.state.db_path == env_db


def test_openapi_contains_expected_paths_and_version(tmp_path: Path) -> None:
    before = set(tmp_path.iterdir())
    app = create_app(db_path=tmp_path / "openapi.db")
    schema = app.openapi()

    assert schema["info"]["title"] == "Incident Triage Copilot API"
    assert schema["info"]["version"] == "0.6.0"
    for path in ["/health", "/ready", "/v1/analyze", "/v1/analyze-bundle", "/v1/history", "/v1/history/{incident_id}"]:
        assert path in schema["paths"]
    assert "AnalyzeRequest" in schema["components"]["schemas"]
    assert "ErrorResponse" in schema["components"]["schemas"]
    assert set(tmp_path.iterdir()) == before


def test_imported_api_module_does_not_create_database(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import importlib

    import incident_triage.api as api_module

    db_path = tmp_path / "import.db"
    monkeypatch.setenv("INCIDENT_TRIAGE_DB", str(db_path))

    importlib.reload(api_module)

    try:
        assert api_module.app.state.db_path == db_path
        assert not db_path.exists()
    finally:
        monkeypatch.delenv("INCIDENT_TRIAGE_DB", raising=False)
        importlib.reload(api_module)


def test_lifespan_without_database_keeps_history_disabled() -> None:
    with TestClient(create_app()) as client:
        health = client.get("/health")
        ready = client.get("/ready")
        analyze = client.post(
            "/v1/analyze",
            json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")},
        )
        history = client.get("/v1/history")

    assert health.status_code == 200
    assert health.json()["history_storage"] == "disabled"
    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "history_storage": "disabled"}
    assert analyze.status_code == 200
    assert analyze.json()["findings"][0]["similar_incidents"] == []
    assert_error(history, 503, "history_storage_disabled")


def test_lifespan_initializes_new_database_and_empty_history(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.db"

    with TestClient(create_app(db_path=db_path)) as client:
        assert db_path.exists()
        assert schema_version(db_path) == DB_SCHEMA_VERSION
        ready = client.get("/ready")
        history = client.get("/v1/history")

    assert ready.status_code == 200
    assert ready.json() == {"status": "ready", "history_storage": "available"}
    assert history.status_code == 200
    assert history.json() == {"schema_version": REPORT_SCHEMA_VERSION, "history_count": 0, "incidents": []}


def test_lifespan_database_initialization_is_idempotent(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"

    with TestClient(create_app(db_path=db_path)) as client:
        first = client.get("/ready")
    with TestClient(create_app(db_path=db_path)) as client:
        second = client.get("/ready")

    assert first.status_code == 200
    assert second.status_code == 200
    assert schema_version(db_path) == DB_SCHEMA_VERSION


def test_lifespan_fails_fast_for_unsupported_schema_version(tmp_path: Path) -> None:
    db_path = tmp_path / "future.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()

    with pytest.raises(StorageError):
        with TestClient(create_app(db_path=db_path)):
            pass


def test_lifespan_fails_fast_for_corrupt_sqlite_file(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_text("not sqlite", encoding="utf-8")

    with pytest.raises(StorageError):
        with TestClient(create_app(db_path=db_path)):
            pass


def test_lifespan_fails_fast_when_database_directory_cannot_be_created(tmp_path: Path) -> None:
    blocking_file = tmp_path / "not-a-directory"
    blocking_file.write_text("occupied", encoding="utf-8")
    db_path = blocking_file / "incidents.db"

    with pytest.raises(StorageError):
        with TestClient(create_app(db_path=db_path)):
            pass


def test_analyze_without_database_returns_empty_similar_incidents() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/v1/analyze",
        json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == REPORT_SCHEMA_VERSION
    assert body["analysis_mode"] == "single"
    assert body["sources"][0]["source_name"] == "api_timeout.log"
    assert body["findings"][0]["similar_incidents"] == []


def test_analyze_with_database_returns_similar_incidents(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    client = TestClient(create_app(db_path=db_path))
    add_response = client.post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "Provider's latency was confirmed.",
        },
    )
    assert add_response.status_code == 201

    response = client.post(
        "/v1/analyze",
        json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")},
    )

    assert response.status_code == 200
    similar = response.json()["findings"][0]["similar_incidents"]
    assert similar[0]["incident_type"] == "external_api_timeout"
    assert similar[0]["match_reasons"] == [
        "incident_type=external_api_timeout",
        "endpoint=https://payments.example.test/v1/charge",
    ]


def test_analyze_with_missing_history_database_treats_history_as_empty(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "missing.db"))

    response = client.post(
        "/v1/analyze",
        json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")},
    )

    assert response.status_code == 200
    assert response.json()["findings"][0]["similar_incidents"] == []


def test_analyze_unknown_incident_is_http_200() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/v1/analyze",
        json={"source_name": "unknown.log", "content": read_fixture("unknown_incident.log")},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "no_incident_detected"


def test_analyze_preserves_unicode() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/v1/analyze",
        json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")},
    )

    assert response.status_code == 200
    assert "zażółć" in response.text


def test_analyze_rejects_empty_content() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze",
        json={"source_name": "empty.log", "content": "   "},
    )

    assert_error(response, 422, "invalid_request")


def test_analyze_rejects_too_large_content() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze",
        json={"source_name": "large.log", "content": "x" * 1_000_001},
    )

    assert_error(response, 422, "invalid_request")


def test_analyze_rejects_invalid_similar_limit() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze",
        json={"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log"), "similar_limit": 21},
    )

    assert_error(response, 422, "invalid_request")


def test_analyze_rejects_path_traversal_source_name() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze",
        json={"source_name": "../secret.log", "content": read_fixture("api_timeout.log")},
    )

    assert_error(response, 422, "invalid_request")


def test_analyze_does_not_read_source_name_from_filesystem() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze",
        json={"source_name": "does/not/exist.log", "content": read_fixture("api_timeout.log")},
    )

    assert response.status_code == 200
    assert response.json()["sources"][0]["source_name"] == "does/not/exist.log"


def test_history_post_stores_single_finding(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "incidents.db"))

    response = client.post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "Provider's latency was confirmed.",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["schema_version"] == REPORT_SCHEMA_VERSION
    assert body["stored_incident"]["incident_type"] == "external_api_timeout"


def test_history_post_selects_incident_type(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "incidents.db"))

    response = client.post(
        "/v1/history",
        json={
            "source_name": "mixed.log",
            "content": read_fixture("mixed.log"),
            "resolution": "Database service was restored.",
            "incident_type": "database_connection_error",
        },
    )

    assert response.status_code == 201
    assert response.json()["stored_incident"]["incident_type"] == "database_connection_error"


def test_history_post_rejects_no_findings(tmp_path: Path) -> None:
    response = TestClient(create_app(db_path=tmp_path / "incidents.db")).post(
        "/v1/history",
        json={
            "source_name": "unknown.log",
            "content": read_fixture("unknown_incident.log"),
            "resolution": "No issue.",
        },
    )

    assert_error(response, 422, "no_incident_detected")


def test_history_post_rejects_ambiguous_selection(tmp_path: Path) -> None:
    response = TestClient(create_app(db_path=tmp_path / "incidents.db")).post(
        "/v1/history",
        json={
            "source_name": "mixed.log",
            "content": read_fixture("mixed.log"),
            "resolution": "Resolved.",
        },
    )

    assert_error(response, 422, "ambiguous_incident_selection")


def test_history_post_rejects_empty_resolution(tmp_path: Path) -> None:
    response = TestClient(create_app(db_path=tmp_path / "incidents.db")).post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "   ",
        },
    )

    assert_error(response, 422, "invalid_request")


def test_history_post_requires_configured_database() -> None:
    response = TestClient(create_app()).post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "Resolved.",
        },
    )

    assert_error(response, 503, "history_storage_disabled")


def test_history_post_preserves_unicode_and_apostrophe(tmp_path: Path) -> None:
    response = TestClient(create_app(db_path=tmp_path / "incidents.db")).post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "Provider's latency zażółć.",
        },
    )

    assert response.status_code == 201
    assert "Provider's latency zażółć." in response.text


def test_history_get_empty_history(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    initialize_database(db_path)
    response = TestClient(create_app(db_path=db_path)).get("/v1/history")

    assert response.status_code == 200
    assert response.json() == {"schema_version": "0.4", "history_count": 0, "incidents": []}


def test_history_get_list_filter_limit_and_record(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "incidents.db"))
    for name, resolution in [
        ("api_timeout.log", "Timeout resolved."),
        ("database_connection_error.log", "Database resolved."),
    ]:
        response = client.post(
            "/v1/history",
            json={"source_name": name, "content": read_fixture(name), "resolution": resolution},
        )
        assert response.status_code == 201

    response = client.get("/v1/history", params={"incident_type": "external_api_timeout", "limit": 1})
    assert response.status_code == 200
    incidents = response.json()["incidents"]
    assert len(incidents) == 1
    assert incidents[0]["incident_type"] == "external_api_timeout"

    record = client.get(f"/v1/history/{incidents[0]['id']}")
    assert record.status_code == 200
    assert record.json()["fingerprint"]


def test_history_get_missing_record_invalid_id_and_no_database(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    initialize_database(db_path)
    client = TestClient(create_app(db_path=db_path))

    assert_error(client.get("/v1/history/999"), 404, "history_record_not_found")
    assert_error(client.get("/v1/history/0"), 422, "invalid_request")
    assert_error(TestClient(create_app()).get("/v1/history"), 503, "history_storage_disabled")


def test_error_contract_has_no_traceback_or_raw_sqlite(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_text("not sqlite", encoding="utf-8")
    response = TestClient(create_app(db_path=db_path)).get("/v1/history")

    assert_error(response, 503, "storage_error")
    text = response.text
    assert "Traceback" not in text
    assert "sqlite" not in response.json()["error"]["message"].lower()


def bundle_payload() -> dict[str, object]:
    return {
        "sources": [
            {"source_name": name, "content": read_fixture(f"bundle/{name}")}
            for name in ["gateway.log", "backend.log", "worker.log", "auth.log", "unrelated.log"]
        ],
        "similar_limit": 3,
    }


def test_analyze_bundle_without_database() -> None:
    response = TestClient(create_app()).post("/v1/analyze-bundle", json=bundle_payload())

    assert response.status_code == 200
    body = response.json()
    assert body["schema_version"] == REPORT_SCHEMA_VERSION
    assert body["analysis_mode"] == "bundle"
    assert body["summary"]["source_count"] == 5
    assert body["summary"]["incident_types"] == ["external_api_timeout", "authorization_failure"]
    assert body["findings"][0]["correlation"]["source_count"] == 4
    assert body["findings"][0]["similar_incidents"] == []


def test_analyze_bundle_with_database_returns_similar_incidents(tmp_path: Path) -> None:
    client = TestClient(create_app(db_path=tmp_path / "incidents.db"))
    create = client.post(
        "/v1/history",
        json={
            "source_name": "api_timeout.log",
            "content": read_fixture("api_timeout.log"),
            "resolution": "Provider's latency was confirmed.",
        },
    )
    assert create.status_code == 201

    response = client.post("/v1/analyze-bundle", json=bundle_payload())

    assert response.status_code == 200
    similar = response.json()["findings"][0]["similar_incidents"]
    assert similar[0]["incident_type"] == "external_api_timeout"
    assert similar[0]["similarity_score"] == 0.7


def test_analyze_bundle_accepts_single_source() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze-bundle",
        json={"sources": [{"source_name": "api_timeout.log", "content": read_fixture("api_timeout.log")}]},
    )

    assert response.status_code == 200
    assert response.json()["analysis_mode"] == "single"


def test_analyze_bundle_validation_errors() -> None:
    client = TestClient(create_app())

    assert_error(client.post("/v1/analyze-bundle", json={"sources": []}), 422, "invalid_request")
    assert_error(
        client.post(
            "/v1/analyze-bundle",
            json={"sources": [{"source_name": f"{index}.log", "content": "x"} for index in range(21)]},
        ),
        422,
        "invalid_request",
    )
    assert_error(
        client.post(
            "/v1/analyze-bundle",
            json={
                "sources": [
                    {"source_name": "same.log", "content": "x"},
                    {"source_name": "same.log", "content": "y"},
                ]
            },
        ),
        422,
        "invalid_request",
    )
    assert_error(
        client.post("/v1/analyze-bundle", json={"sources": [{"source_name": "empty.log", "content": "   "}]}),
        422,
        "invalid_request",
    )
    assert_error(
        client.post(
            "/v1/analyze-bundle",
            json={"sources": [{"source_name": "large.log", "content": "x" * 1_000_001}]},
        ),
        422,
        "invalid_request",
    )
    assert_error(
        client.post(
            "/v1/analyze-bundle",
            json={"sources": [{"source_name": f"{index}.log", "content": "x" * 900_000} for index in range(6)]},
        ),
        422,
        "invalid_request",
    )
    assert_error(
        client.post("/v1/analyze-bundle", json={"sources": [{"source_name": "../x.log", "content": "x"}]}),
        422,
        "invalid_request",
    )


def test_analyze_bundle_unicode_and_no_filesystem_read() -> None:
    response = TestClient(create_app()).post(
        "/v1/analyze-bundle",
        json={
            "sources": [
                {
                    "source_name": "does/not/exist.log",
                    "content": "2026-07-15T13:00:05Z ERROR request_id=abc-1 user=zażółć payment API timed out endpoint=https://payments.example.test/v1/charge",
                }
            ]
        },
    )

    assert response.status_code == 200
    assert response.json()["sources"][0]["source_name"] == "does/not/exist.log"
    assert "zażółć" in response.text


def assert_error(response, status_code: int, code: str) -> None:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert "message" in body["error"]
    assert "details" in body["error"]
    assert "Traceback" not in response.text
