from __future__ import annotations

from pathlib import Path

import pytest

from incident_triage.analyzer import analyze_log
from incident_triage.models import LogSource
from incident_triage.service import (
    ServiceError,
    analyze_log_file,
    analyze_log_text,
    analyze_sources,
    select_finding,
    store_resolved_incident_from_text,
)

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_text_and_file_analysis_share_same_findings() -> None:
    path = Path("fixtures/api_timeout.log")
    content = path.read_text(encoding="utf-8")

    from_file = analyze_log_file(path)
    from_text = analyze_log_text(content, source_name=path.as_posix())

    assert from_file["findings"] == from_text["findings"]
    assert analyze_log(path)["findings"] == from_text["findings"]


def test_store_history_from_text_does_not_depend_on_cli(tmp_path: Path) -> None:
    content = (FIXTURES / "api_timeout.log").read_text(encoding="utf-8")

    stored = store_resolved_incident_from_text(
        content,
        source_name="api_timeout.log",
        db_path=tmp_path / "incidents.db",
        resolution="Provider's latency was confirmed.",
    )

    assert stored["incident_type"] == "external_api_timeout"
    assert stored["resolution"] == "Provider's latency was confirmed."


def test_select_finding_is_transport_independent() -> None:
    report = analyze_log_file(Path("fixtures/mixed.log"))

    selected = select_finding(report["findings"], "database_connection_error")

    assert selected["incident_type"] == "database_connection_error"


def bundle_sources() -> list[LogSource]:
    names = ["gateway.log", "backend.log", "worker.log", "auth.log", "unrelated.log"]
    return [LogSource(name, (FIXTURES / "bundle" / name).read_text(encoding="utf-8")) for name in names]


def test_single_analysis_uses_multi_source_mechanism() -> None:
    content = (FIXTURES / "api_timeout.log").read_text(encoding="utf-8")

    single = analyze_log_text(content, "api_timeout.log")
    bundled = analyze_sources([LogSource("api_timeout.log", content)])

    assert single == bundled
    assert single["analysis_mode"] == "single"


def test_bundle_preserves_source_order_and_is_deterministic_without_mutation() -> None:
    sources = bundle_sources()
    original = list(sources)

    first = analyze_sources(sources)
    second = analyze_sources(sources)

    assert sources == original
    assert first == second
    assert first["analysis_mode"] == "bundle"
    assert [source["source_name"] for source in first["sources"]] == [
        "gateway.log",
        "backend.log",
        "worker.log",
        "auth.log",
        "unrelated.log",
    ]


def test_duplicate_source_name_and_limits_are_rejected() -> None:
    with pytest.raises(ServiceError):
        analyze_sources([LogSource("same.log", "x"), LogSource("same.log", "y")])
    with pytest.raises(ServiceError):
        analyze_sources([LogSource(f"{index}.log", "x") for index in range(21)])
    with pytest.raises(ServiceError):
        analyze_sources([LogSource("empty.log", "   ")])
    with pytest.raises(ServiceError):
        analyze_sources([LogSource("large.log", "x" * 1_000_001)])


def test_cross_source_request_id_correlation() -> None:
    report = analyze_sources(bundle_sources())
    timeout = report["findings"][0]

    assert timeout["incident_type"] == "external_api_timeout"
    assert timeout["evidence"] == [
        {
            "source_name": "worker.log",
            "line_number": 1,
            "text": "2026-07-15T13:00:05Z ERROR request_id=req-42 service=worker payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge",
        }
    ]
    assert [item["source_name"] for item in timeout["context"]] == [
        "gateway.log",
        "backend.log",
        "auth.log",
        "backend.log",
    ]
    assert all("req-other" not in item["text"] for item in timeout["context"])
    assert all("req-77" not in item["text"] for item in timeout["context"])
    assert timeout["correlation"] == {
        "strategy": "request_id",
        "key": "req-42",
        "window_seconds": None,
        "source_count": 4,
    }


def test_cross_source_time_window_correlation() -> None:
    report = analyze_sources(bundle_sources())
    auth = [finding for finding in report["findings"] if finding["incident_type"] == "authorization_failure"][0]

    assert auth["confidence"] == 0.9
    assert auth["correlation"] == {
        "strategy": "time_window",
        "key": None,
        "window_seconds": 30,
        "source_count": 2,
    }
    assert auth["context"] == [
        {
            "source_name": "backend.log",
            "line_number": 2,
            "text": "2026-07-15T13:10:05Z INFO service=backend worker=orders preparing reconciliation",
        },
    ]
    evidence_keys = {(item["source_name"], item["line_number"]) for item in auth["evidence"]}
    context_keys = {(item["source_name"], item["line_number"]) for item in auth["context"]}
    assert evidence_keys.isdisjoint(context_keys)
    assert all("outside time window" not in item["text"] for item in auth["context"])
