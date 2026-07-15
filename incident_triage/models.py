from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class NormalizedEvent:
    source_name: str
    source_index: int
    line_number: int
    raw: str
    timestamp: str | None
    level: str | None
    message: str | None
    request_id: str | None
    attributes: dict[str, str]
    timestamp_value: datetime | None = None


@dataclass(frozen=True)
class LogLine:
    number: int
    text: str


@dataclass(frozen=True)
class LogSource:
    source_name: str
    content: str


@dataclass(frozen=True)
class Evidence:
    source_name: str
    source_index: int
    line_number: int
    text: str

    @classmethod
    def from_event(cls, event: NormalizedEvent) -> Evidence:
        return cls(
            source_name=event.source_name,
            source_index=event.source_index,
            line_number=event.line_number,
            text=event.raw,
        )

    def to_json(self) -> dict[str, object]:
        return {"source_name": self.source_name, "line_number": self.line_number, "text": self.text}


@dataclass(frozen=True)
class Correlation:
    strategy: str
    key: str | None
    window_seconds: int | None
    source_count: int

    def to_json(self) -> dict[str, object]:
        return {
            "strategy": self.strategy,
            "key": self.key,
            "window_seconds": self.window_seconds,
            "source_count": self.source_count,
        }


@dataclass(frozen=True)
class Finding:
    incident_type: str
    symptom: str
    probable_cause: str
    evidence: tuple[Evidence, ...]
    context: tuple[Evidence, ...]
    recommended_actions: tuple[str, ...]
    confidence: float
    correlation: Correlation
    similar_incidents: tuple[dict[str, Any], ...] = ()

    def to_json(self) -> dict[str, object]:
        return {
            "incident_type": self.incident_type,
            "symptom": self.symptom,
            "probable_cause": self.probable_cause,
            "evidence": [item.to_json() for item in self.evidence],
            "context": [item.to_json() for item in self.context],
            "recommended_actions": list(self.recommended_actions),
            "confidence": self.confidence,
            "correlation": self.correlation.to_json(),
            "similar_incidents": list(self.similar_incidents),
        }
