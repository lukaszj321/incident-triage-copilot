from __future__ import annotations

import hashlib
import json
from typing import Any

from incident_triage.parser import normalize_line

STABLE_ATTRIBUTE_ALLOWLIST = {
    "endpoint",
    "host",
    "port",
    "status",
    "response_status",
    "service",
    "provider",
    "worker",
}


def stable_attributes_from_finding(finding: dict[str, Any]) -> dict[str, str]:
    stable: dict[str, str] = {}
    for item in finding.get("evidence", []):
        event = normalize_line(int(item["line_number"]), str(item["text"]))
        for key, value in event.attributes.items():
            if key in STABLE_ATTRIBUTE_ALLOWLIST:
                stable[key] = value
    return dict(sorted(stable.items()))


def fingerprint_for_finding(finding: dict[str, Any]) -> str:
    payload = {
        "incident_type": finding["incident_type"],
        "evidence_signal": evidence_signal(finding),
        "stable_attributes": stable_attributes_from_finding(finding),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evidence_signal(finding: dict[str, Any]) -> list[str]:
    signals: list[str] = []
    for item in finding.get("evidence", []):
        event = normalize_line(int(item["line_number"]), str(item["text"]))
        signal = " ".join(part for part in [event.level, event.message] if part)
        signals.append(" ".join(signal.lower().split()))
    return signals


def similarity_for_candidate(
    current_incident_type: str,
    current_attributes: dict[str, str],
    candidate: dict[str, Any],
) -> tuple[float, list[str]]:
    reasons = [f"incident_type={current_incident_type}"]
    candidate_attributes = candidate["stable_attributes"]
    matching_attributes = [
        (key, value) for key, value in sorted(current_attributes.items()) if candidate_attributes.get(key) == value
    ]
    reasons.extend(f"{key}={value}" for key, value in matching_attributes)

    attribute_score = min(0.4, 0.1 * len(matching_attributes))
    return round(0.6 + attribute_score, 10), reasons
