from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from incident_triage.models import Correlation, Evidence, Finding, LogSource, NormalizedEvent
from incident_triage.parser import parse_log_text
from incident_triage.rules import RULES, Rule
from incident_triage.versions import REPORT_SCHEMA_VERSION

TIME_WINDOW_SECONDS = 30


def analyze_log(path: Path) -> dict[str, object]:
    content = path.read_text(encoding="utf-8")
    return analyze_text(content, source_name=path)


def analyze_text(content: str, source_name: str | Path) -> dict[str, object]:
    return analyze_sources([LogSource(source_name=format_source_name(source_name), content=content)])


def analyze_sources(sources: list[LogSource]) -> dict[str, object]:
    events: list[NormalizedEvent] = []
    source_summaries: list[dict[str, object]] = []
    for source_index, source in enumerate(sources):
        source_events = parse_log_text(
            source.content,
            source_name=source.source_name,
            source_index=source_index,
        )
        events.extend(source_events)
        source_summaries.append(
            {
                "source_name": source.source_name,
                "line_count": len(source.content.splitlines()),
            }
        )

    findings = detect_findings(events)
    findings = sorted(
        findings,
        key=lambda finding: (
            finding.evidence[0].source_index,
            finding.evidence[0].line_number,
            rule_index(finding.incident_type),
        ),
    )
    return build_report(source_summaries=source_summaries, findings=findings)


def detect_findings(events: list[NormalizedEvent], rules: tuple[Rule, ...] = RULES) -> list[Finding]:
    findings: list[Finding] = []

    for rule in rules:
        matching_events = [event for event in events if rule.pattern.search(event.raw)]
        for evidence_events in group_evidence_events(matching_events):
            context_events, correlation = correlate_events(events, evidence_events)
            evidence = tuple(Evidence.from_event(event) for event in sort_evidence_events(evidence_events))
            context = tuple(Evidence.from_event(event) for event in sort_context_events(context_events))
            findings.append(
                Finding(
                    incident_type=rule.incident_type,
                    symptom=rule.symptom,
                    probable_cause=rule.probable_cause,
                    evidence=evidence,
                    context=context,
                    recommended_actions=rule.recommended_actions,
                    confidence=rule.confidence,
                    correlation=correlation,
                )
            )

    return findings


def build_report(source_summaries: list[dict[str, object]], findings: list[Finding]) -> dict[str, object]:
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "analysis_mode": "single" if len(source_summaries) == 1 else "bundle",
        "sources": source_summaries,
        "status": "incidents_detected" if findings else "no_incident_detected",
        "summary": {
            "source_count": len(source_summaries),
            "findings_count": len(findings),
            "incident_types": [finding.incident_type for finding in findings],
        },
        "findings": [finding.to_json() for finding in findings],
    }


def format_source_name(source: str | Path) -> str:
    if isinstance(source, Path):
        return source.as_posix()
    return source.replace("\\", "/")


def group_evidence_events(events: list[NormalizedEvent]) -> list[tuple[NormalizedEvent, ...]]:
    request_id_groups: dict[str, list[NormalizedEvent]] = defaultdict(list)
    standalone_groups: list[tuple[NormalizedEvent, ...]] = []

    for event in events:
        if event.request_id:
            request_id_groups[event.request_id].append(event)
        else:
            standalone_groups.append((event,))

    grouped = [tuple(group) for group in request_id_groups.values()]
    grouped.extend(standalone_groups)
    return sorted(grouped, key=lambda group: (group[0].source_index, group[0].line_number))


def correlate_events(
    all_events: list[NormalizedEvent], evidence_events: tuple[NormalizedEvent, ...]
) -> tuple[list[NormalizedEvent], Correlation]:
    request_id = first_request_id(evidence_events)
    evidence_lines = event_keys(evidence_events)

    if request_id:
        context = [
            event for event in all_events if event.request_id == request_id and event_key(event) not in evidence_lines
        ]
        return context, Correlation(
            strategy="request_id",
            key=request_id,
            window_seconds=None,
            source_count=count_sources(evidence_events, context),
        )

    timestamped_evidence = [event for event in evidence_events if event.timestamp_value is not None]
    if timestamped_evidence:
        context = [
            event
            for event in all_events
            if event_key(event) not in evidence_lines
            and event.request_id is None
            and event.timestamp_value is not None
            and is_within_time_window(event, timestamped_evidence)
        ]
        return context, Correlation(
            strategy="time_window",
            key=None,
            window_seconds=TIME_WINDOW_SECONDS,
            source_count=count_sources(evidence_events, context),
        )

    return [], Correlation(
        strategy="none",
        key=None,
        window_seconds=None,
        source_count=count_sources(evidence_events, []),
    )


def first_request_id(events: tuple[NormalizedEvent, ...]) -> str | None:
    for event in events:
        if event.request_id:
            return event.request_id
    return None


def is_within_time_window(event: NormalizedEvent, evidence_events: list[NormalizedEvent]) -> bool:
    assert event.timestamp_value is not None
    window = timedelta(seconds=TIME_WINDOW_SECONDS)
    return any(
        evidence.timestamp_value is not None and abs(event.timestamp_value - evidence.timestamp_value) <= window
        for evidence in evidence_events
    )


def sort_evidence_events(events: tuple[NormalizedEvent, ...]) -> list[NormalizedEvent]:
    return sorted(events, key=lambda event: (event.source_index, event.line_number))


def sort_context_events(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    return sorted(
        events,
        key=lambda event: (
            event.timestamp_value is None,
            event.timestamp_value or "",
            event.source_index,
            event.line_number,
        ),
    )


def count_sources(evidence_events: tuple[NormalizedEvent, ...], context_events: list[NormalizedEvent]) -> int:
    return len({event.source_name for event in [*evidence_events, *context_events]})


def event_key(event: NormalizedEvent) -> tuple[str, int]:
    return event.source_name, event.line_number


def event_keys(events: tuple[NormalizedEvent, ...]) -> set[tuple[str, int]]:
    return {event_key(event) for event in events}


def rule_index(incident_type: str) -> int:
    for index, rule in enumerate(RULES):
        if rule.incident_type == incident_type:
            return index
    return len(RULES)
