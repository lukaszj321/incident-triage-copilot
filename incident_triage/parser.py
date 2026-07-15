from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path

from incident_triage.models import NormalizedEvent

TIMESTAMP_RE = re.compile(r"^(?P<timestamp>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)\b")
LEVELS = {"DEBUG", "INFO", "WARN", "WARNING", "ERROR", "CRITICAL"}
KEY_VALUE_RE = re.compile(r"(?P<key>[A-Za-z_][\w.-]*)=(?P<value>\"[^\"]*\"|'[^']*'|\S+)")


def parse_log_file(path: Path) -> list[NormalizedEvent]:
    content = path.read_text(encoding="utf-8")
    return parse_log_text(content, source_name=path.as_posix())


def parse_log_text(content: str, source_name: str = "log", source_index: int = 0) -> list[NormalizedEvent]:
    return [
        normalize_line(index, line, source_name=source_name, source_index=source_index)
        for index, line in enumerate(content.splitlines(), start=1)
    ]


def normalize_line(line_number: int, raw: str, source_name: str = "log", source_index: int = 0) -> NormalizedEvent:
    rest = raw
    timestamp_text: str | None = None
    timestamp_value: datetime | None = None

    timestamp_match = TIMESTAMP_RE.match(rest)
    if timestamp_match:
        parsed = parse_timestamp(timestamp_match.group("timestamp"))
        if parsed is not None:
            timestamp_text, timestamp_value = parsed
        rest = rest[timestamp_match.end() :].lstrip()

    level: str | None = None
    parts = rest.split(maxsplit=1)
    if parts and parts[0].upper() in LEVELS:
        level = "WARN" if parts[0].upper() == "WARNING" else parts[0].upper()
        rest = parts[1] if len(parts) > 1 else ""

    attributes = parse_attributes(rest)
    request_id = attributes.get("request_id")
    message = build_message(rest)

    return NormalizedEvent(
        source_name=source_name,
        source_index=source_index,
        line_number=line_number,
        raw=raw,
        timestamp=timestamp_text,
        level=level,
        message=message,
        request_id=request_id,
        attributes=attributes,
        timestamp_value=timestamp_value,
    )


def parse_timestamp(value: str) -> tuple[str, datetime] | None:
    normalized = value.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized_for_parse = normalized[:-1] + "+00:00"
    else:
        normalized_for_parse = normalized

    try:
        parsed = datetime.fromisoformat(normalized_for_parse)
    except ValueError:
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    else:
        parsed = parsed.astimezone(UTC)

    timestamp_text = parsed.isoformat().replace("+00:00", "Z")
    return timestamp_text, parsed


def parse_attributes(text: str) -> dict[str, str]:
    attributes: dict[str, str] = {}
    for match in KEY_VALUE_RE.finditer(text):
        value = match.group("value")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        attributes[match.group("key")] = value
    return attributes


def build_message(text: str) -> str | None:
    message = KEY_VALUE_RE.sub("", text)
    message = " ".join(message.split())
    return message or None
