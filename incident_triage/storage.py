from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from incident_triage.similarity import (
    fingerprint_for_finding,
    similarity_for_candidate,
    stable_attributes_from_finding,
)
from incident_triage.versions import REPORT_SCHEMA_VERSION

DB_SCHEMA_VERSION = 1


class StorageError(Exception):
    pass


class DatabaseNotFoundError(StorageError):
    pass


class UnsupportedSchemaVersionError(StorageError):
    pass


def initialize_database(db_path: Path) -> None:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise StorageError("could not create SQLite database directory") from exc
    with connect(db_path) as connection:
        ensure_supported_schema(connection)
        configure_writable_database(connection)
        try:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS incidents (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    incident_type TEXT NOT NULL,
                    symptom TEXT NOT NULL,
                    probable_cause TEXT NOT NULL,
                    resolution TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_file TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    stable_attributes TEXT NOT NULL,
                    fingerprint TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_incidents_incident_type ON incidents (incident_type)")
            connection.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"could not initialize SQLite schema: {exc}") from exc


def add_incident(
    db_path: Path,
    finding: dict[str, Any],
    source_file: str,
    resolution: str,
    created_at: str | None = None,
) -> dict[str, Any]:
    initialize_database(db_path)
    stable_attributes = stable_attributes_from_finding(finding)
    evidence_json = dump_json(finding["evidence"])
    stable_attributes_json = dump_json(stable_attributes)
    fingerprint = fingerprint_for_finding(finding)
    created_at_value = created_at or datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    with connect(db_path) as connection:
        ensure_supported_schema(connection)
        try:
            cursor = connection.execute(
                """
                INSERT INTO incidents (
                    incident_type,
                    symptom,
                    probable_cause,
                    resolution,
                    confidence,
                    source_file,
                    evidence,
                    stable_attributes,
                    fingerprint,
                    created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    finding["incident_type"],
                    finding["symptom"],
                    finding["probable_cause"],
                    resolution,
                    finding["confidence"],
                    source_file,
                    evidence_json,
                    stable_attributes_json,
                    fingerprint,
                    created_at_value,
                ),
            )
        except sqlite3.DatabaseError as exc:
            raise StorageError(f"could not store incident history: {exc}") from exc
        if cursor.lastrowid is None:
            raise StorageError("SQLite did not return an inserted incident id.")
        incident_id = int(cursor.lastrowid)

    stored = get_incident(db_path, incident_id)
    assert stored is not None
    return stored


def get_incident(db_path: Path, incident_id: int) -> dict[str, Any] | None:
    with connect_existing(db_path) as connection:
        row = connection.execute("SELECT * FROM incidents WHERE id = ?", (incident_id,)).fetchone()
    return row_to_incident(row) if row else None


def list_history(db_path: Path, limit: int | None = None, incident_type: str | None = None) -> list[dict[str, Any]]:
    params: list[Any] = []
    where = ""
    if incident_type is not None:
        where = "WHERE incident_type = ?"
        params.append(incident_type)

    limit_clause = ""
    if limit is not None:
        limit_clause = "LIMIT ?"
        params.append(limit)

    with connect_existing(db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT * FROM incidents
            {where}
            ORDER BY created_at DESC, id DESC
            {limit_clause}
            """,
            params,
        ).fetchall()
    return [row_to_incident(row) for row in rows]


def find_candidates_by_type(db_path: Path, incident_type: str) -> list[dict[str, Any]]:
    with connect_existing(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM incidents
            WHERE incident_type = ?
            """,
            (incident_type,),
        ).fetchall()
    return [row_to_incident(row) for row in rows]


def find_similar_incidents(db_path: Path, finding: dict[str, Any], limit: int = 3) -> list[dict[str, Any]]:
    current_attributes = stable_attributes_from_finding(finding)
    candidates = find_candidates_by_type(db_path, str(finding["incident_type"]))
    matches: list[dict[str, Any]] = []

    for candidate in candidates:
        score, reasons = similarity_for_candidate(str(finding["incident_type"]), current_attributes, candidate)
        matches.append(
            {
                "id": candidate["id"],
                "incident_type": candidate["incident_type"],
                "similarity_score": score,
                "match_reasons": reasons,
                "symptom": candidate["symptom"],
                "probable_cause": candidate["probable_cause"],
                "resolution": candidate["resolution"],
                "source_file": candidate["source_file"],
                "created_at": candidate["created_at"],
            }
        )

    return sorted(matches, key=similarity_sort_key)[:limit]


def similarity_sort_key(item: dict[str, Any]) -> tuple[float, float, int]:
    created_at = datetime.fromisoformat(str(item["created_at"]).replace("Z", "+00:00"))
    return (-float(item["similarity_score"]), -created_at.timestamp(), int(item["id"]))


def schema_version(db_path: Path) -> int:
    with connect_existing(db_path) as connection:
        return get_user_version(connection)


def connect_existing(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise DatabaseNotFoundError(f"database file does not exist: {db_path}")
    connection = connect(db_path)
    ensure_supported_schema(connection)
    return connection


def connect(db_path: Path) -> sqlite3.Connection:
    try:
        connection = sqlite3.connect(db_path, timeout=5.0)
    except sqlite3.Error as exc:
        raise StorageError(f"could not open SQLite database: {exc}") from exc
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
    except sqlite3.Error as exc:
        connection.close()
        raise StorageError(f"could not configure SQLite connection: {exc}") from exc
    return connection


def configure_writable_database(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.Error as exc:
        raise StorageError(f"could not enable SQLite WAL mode: {exc}") from exc


def readiness_check(db_path: Path) -> bool:
    try:
        with connect_existing(db_path) as connection:
            connection.execute("SELECT 1").fetchone()
    except StorageError:
        return False
    except sqlite3.DatabaseError:
        return False
    return True


def ensure_supported_schema(connection: sqlite3.Connection) -> None:
    try:
        version = get_user_version(connection)
    except sqlite3.DatabaseError as exc:
        raise StorageError(f"could not read SQLite schema version: {exc}") from exc
    if version > DB_SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(
            f"unsupported SQLite schema version: {version}; supported version is {DB_SCHEMA_VERSION}"
        )


def get_user_version(connection: sqlite3.Connection) -> int:
    return int(connection.execute("PRAGMA user_version").fetchone()[0])


def row_to_incident(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "incident_type": row["incident_type"],
        "symptom": row["symptom"],
        "probable_cause": row["probable_cause"],
        "resolution": row["resolution"],
        "confidence": row["confidence"],
        "source_file": row["source_file"],
        "evidence": load_json(row["evidence"]),
        "stable_attributes": load_json(row["stable_attributes"]),
        "fingerprint": row["fingerprint"],
        "created_at": row["created_at"],
    }


def dump_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def load_json(value: str) -> Any:
    return json.loads(value)


def history_report(db_path: Path, limit: int | None = None, incident_type: str | None = None) -> dict[str, Any]:
    incidents = [
        {
            "id": incident["id"],
            "incident_type": incident["incident_type"],
            "resolution": incident["resolution"],
            "source_file": incident["source_file"],
            "created_at": incident["created_at"],
        }
        for incident in list_history(db_path, limit=limit, incident_type=incident_type)
    ]
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "history_count": len(incidents),
        "incidents": incidents,
    }
