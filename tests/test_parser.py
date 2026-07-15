from __future__ import annotations

from incident_triage.parser import parse_log_text


def test_normalizes_structured_log_line_and_source_name() -> None:
    raw = (
        "2026-07-15T09:14:05Z ERROR request_id=abc-1 user=zażółć "
        "upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge"
    )

    event = parse_log_text(raw, source_name="worker.log", source_index=2)[0]

    assert event.source_name == "worker.log"
    assert event.source_index == 2
    assert event.line_number == 1
    assert event.raw == raw
    assert event.timestamp == "2026-07-15T09:14:05Z"
    assert event.timestamp_value is not None
    assert event.level == "ERROR"
    assert event.request_id == "abc-1"
    assert event.attributes["request_id"] == "abc-1"
    assert event.attributes["user"] == "zażółć"
    assert event.attributes["endpoint"] == "https://payments.example.test/v1/charge"
    assert event.message == "upstream payment API timed out after 3000ms"


def test_line_numbers_start_at_one() -> None:
    events = parse_log_text("first line\nsecond line", source_name="gateway.log")

    assert [event.line_number for event in events] == [1, 2]
    assert [event.source_name for event in events] == ["gateway.log", "gateway.log"]


def test_preserves_partially_unstructured_line() -> None:
    raw = "partially structured line without timestamp but with request_id=noise-1 and no incident terms"

    event = parse_log_text(raw, source_name="unknown.log")[0]

    assert event.source_name == "unknown.log"
    assert event.line_number == 1
    assert event.raw == raw
    assert event.timestamp is None
    assert event.timestamp_value is None
    assert event.level is None
    assert event.request_id == "noise-1"
    assert event.attributes == {"request_id": "noise-1"}
    assert event.message == "partially structured line without timestamp but with and no incident terms"
