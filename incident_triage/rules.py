from __future__ import annotations

import re
from dataclasses import dataclass
from re import Pattern


@dataclass(frozen=True)
class Rule:
    incident_type: str
    symptom: str
    probable_cause: str
    pattern: Pattern[str]
    recommended_actions: tuple[str, ...]
    confidence: float


def compile_pattern(required_terms: tuple[str, ...]) -> Pattern[str]:
    lookaheads = "".join(f"(?=.*(?:{term}))" for term in required_terms)
    return re.compile(lookaheads + r".+", re.IGNORECASE)


RULES: tuple[Rule, ...] = (
    Rule(
        incident_type="external_api_timeout",
        symptom="Timeout while calling an external API or upstream HTTP service.",
        probable_cause="The external API or upstream HTTP service did not respond before the configured timeout.",
        pattern=compile_pattern(
            (
                r"\b(timeout|timed out|deadline exceeded|read timeout)\b",
                r"\b(api|http|https|upstream|external|endpoint|webhook)\b",
            )
        ),
        recommended_actions=(
            "Check the referenced upstream API endpoint and provider status.",
            "Inspect client timeout settings and retry behavior for the affected request.",
            "Correlate this line with surrounding request identifiers before changing production settings.",
        ),
        confidence=0.9,
    ),
    Rule(
        incident_type="database_connection_error",
        symptom="Application failed to establish a database connection.",
        probable_cause="The database connection attempt failed according to the matching log line.",
        pattern=compile_pattern(
            (
                r"\b(database|db|postgres|postgresql|mysql|mariadb|sql server|sqlite|mongodb|redis)\b",
                r"\b(connection refused|could not connect|connection failed|connect failed|"
                r"unable to connect|lost connection)\b",
            )
        ),
        recommended_actions=(
            "Verify database host, port, and network reachability from the affected service.",
            "Check database availability and connection limits.",
            "Review recent deployment or configuration changes that affect database connectivity.",
        ),
        confidence=0.9,
    ),
    Rule(
        incident_type="authorization_failure",
        symptom="Authorization or authentication failed.",
        probable_cause=(
            "The request was denied because the log contains an explicit authorization or authentication failure."
        ),
        pattern=compile_pattern(
            (
                r"\b(auth|authorization|authentication|login|token|jwt|oauth|credential|credentials)\b",
                r"\b(failed|failure|denied|unauthorized|forbidden|invalid|expired|401|403)\b",
            )
        ),
        recommended_actions=(
            "Verify token, credential, or authorization policy for the affected principal.",
            "Check whether the failure is isolated to one user or affects multiple requests.",
            "Review identity provider or access-control logs for the same timestamp.",
        ),
        confidence=0.9,
    ),
)
