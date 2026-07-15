from __future__ import annotations

from pathlib import Path

from incident_triage.analyzer import analyze_log

FIXTURES = Path(__file__).parents[1] / "fixtures"


def incident_types(report: dict[str, object]) -> list[str]:
    return report["summary"]["incident_types"]  # type: ignore[index,return-value]


def test_external_api_timeout_includes_source_evidence_context_and_request_id_correlation() -> None:
    source_name = (FIXTURES / "api_timeout.log").as_posix()
    report = analyze_log(FIXTURES / "api_timeout.log")

    assert report["schema_version"] == "0.4"
    assert report["analysis_mode"] == "single"
    assert report["sources"] == [{"source_name": source_name, "line_count": 4}]
    assert report["status"] == "incidents_detected"
    assert report["summary"] == {
        "source_count": 1,
        "findings_count": 1,
        "incident_types": ["external_api_timeout"],
    }
    assert "source_file" not in report

    finding = report["findings"][0]  # type: ignore[index]
    assert set(finding) == {
        "incident_type",
        "symptom",
        "probable_cause",
        "evidence",
        "context",
        "recommended_actions",
        "confidence",
        "correlation",
        "similar_incidents",
    }
    assert finding["evidence"] == [
        {
            "source_name": source_name,
            "line_number": 2,
            "text": "2026-07-15T09:14:05Z ERROR request_id=abc-1 upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge",
        }
    ]
    assert finding["context"] == [
        {
            "source_name": source_name,
            "line_number": 1,
            "text": "2026-07-15T09:14:02Z INFO request_id=abc-1 user=zażółć start checkout submit",
        },
        {
            "source_name": source_name,
            "line_number": 3,
            "text": "2026-07-15T09:14:05Z INFO request_id=abc-1 response_status=502",
        },
    ]
    assert finding["correlation"] == {
        "strategy": "request_id",
        "key": "abc-1",
        "window_seconds": None,
        "source_count": 1,
    }
    assert finding["similar_incidents"] == []


def test_database_connection_error_uses_time_window_correlation() -> None:
    source_name = (FIXTURES / "database_connection_error.log").as_posix()
    report = analyze_log(FIXTURES / "database_connection_error.log")

    assert incident_types(report) == ["database_connection_error"]
    finding = report["findings"][0]  # type: ignore[index]
    assert finding["evidence"] == [
        {
            "source_name": source_name,
            "line_number": 3,
            "text": "2026-07-15T10:21:11Z ERROR worker=orders database connection refused host=db.internal port=5432",
        }
    ]
    assert finding["context"] == [
        {
            "source_name": source_name,
            "line_number": 2,
            "text": "2026-07-15T10:21:00Z INFO worker=orders starting reconciliation",
        },
        {
            "source_name": source_name,
            "line_number": 4,
            "text": "2026-07-15T10:21:20Z WARN worker=orders retry scheduled in 30s",
        },
    ]
    assert finding["correlation"] == {
        "strategy": "time_window",
        "key": None,
        "window_seconds": 30,
        "source_count": 1,
    }


def test_authorization_failure_includes_exact_evidence_line() -> None:
    source_name = (FIXTURES / "authorization_failure.log").as_posix()
    report = analyze_log(FIXTURES / "authorization_failure.log")

    assert incident_types(report) == ["authorization_failure"]
    finding = report["findings"][0]  # type: ignore[index]
    expected_text = (
        "2026-07-15T11:30:01Z WARN request_id=login-7 authorization denied invalid token subject=lukasz status=403"
    )
    assert finding["evidence"] == [
        {
            "source_name": source_name,
            "line_number": 2,
            "text": expected_text,
        }
    ]
    assert finding["correlation"] == {
        "strategy": "request_id",
        "key": "login-7",
        "window_seconds": None,
        "source_count": 1,
    }


def test_mixed_log_reports_multiple_supported_scenarios_deterministically() -> None:
    report = analyze_log(FIXTURES / "mixed.log")

    assert report["schema_version"] == "0.4"
    assert report["status"] == "incidents_detected"
    assert incident_types(report) == [
        "external_api_timeout",
        "database_connection_error",
        "authorization_failure",
    ]
    assert report["summary"]["findings_count"] == 3  # type: ignore[index]
    assert [finding["evidence"][0]["line_number"] for finding in report["findings"]] == [2, 5, 7]  # type: ignore[index]


def test_unknown_incident_has_empty_findings() -> None:
    report = analyze_log(FIXTURES / "unknown_incident.log")

    assert report == {
        "schema_version": "0.4",
        "analysis_mode": "single",
        "sources": [{"source_name": (FIXTURES / "unknown_incident.log").as_posix(), "line_count": 4}],
        "status": "no_incident_detected",
        "summary": {"source_count": 1, "findings_count": 0, "incident_types": []},
        "findings": [],
    }


def test_analysis_is_deterministic_for_same_input() -> None:
    path = FIXTURES / "mixed.log"

    assert analyze_log(path) == analyze_log(path)


def test_context_never_duplicates_evidence() -> None:
    report = analyze_log(FIXTURES / "mixed.log")

    for finding in report["findings"]:  # type: ignore[union-attr]
        evidence_keys = {(item["source_name"], item["line_number"]) for item in finding["evidence"]}
        context_keys = {(item["source_name"], item["line_number"]) for item in finding["context"]}
        assert evidence_keys.isdisjoint(context_keys)


def test_time_window_context_does_not_create_extra_finding() -> None:
    report = analyze_log(FIXTURES / "database_connection_error.log")

    assert report["summary"]["findings_count"] == 1  # type: ignore[index]
    assert incident_types(report) == ["database_connection_error"]
