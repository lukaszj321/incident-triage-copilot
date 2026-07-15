from __future__ import annotations

import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from incident_triage.cli import main
from incident_triage.versions import REPORT_SCHEMA_VERSION

FIXTURES = Path(__file__).parents[1] / "fixtures"


def test_cli_prints_json_report() -> None:
    stdout = io.StringIO()

    exit_code = main([str(FIXTURES / "api_timeout.log")], stdout=stdout)

    assert exit_code == 0
    report = json.loads(stdout.getvalue())
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["status"] == "incidents_detected"
    assert report["summary"]["incident_types"] == ["external_api_timeout"]
    assert report["findings"][0]["incident_type"] == "external_api_timeout"


def test_cli_preserves_unicode_in_json_output() -> None:
    stdout = io.StringIO()

    exit_code = main([str(FIXTURES / "api_timeout.log")], stdout=stdout)

    assert exit_code == 0
    assert "zażółć" in stdout.getvalue()
    report = json.loads(stdout.getvalue())
    assert report["findings"][0]["context"][0]["text"].endswith("start checkout submit")


def test_cli_rejects_missing_file() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = main([str(FIXTURES / "missing.log")], stdout=stdout, stderr=stderr)

    assert exit_code == 2
    assert stdout.getvalue() == ""
    assert "does not exist" in stderr.getvalue()


def test_cli_missing_argument_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main([])

    captured = capsys.readouterr()
    assert exc_info.value.code == 2
    assert "usage:" in captured.err
    assert "command" in captured.err


def test_cli_legacy_relative_path_uses_forward_slashes() -> None:
    stdout = io.StringIO()

    exit_code = main(["fixtures/api_timeout.log"], stdout=stdout)

    assert exit_code == 0
    report = json.loads(stdout.getvalue())
    assert report["sources"][0]["source_name"] == "fixtures/api_timeout.log"


def test_cli_analyze_subcommand_without_database() -> None:
    stdout = io.StringIO()

    exit_code = main(["analyze", "fixtures/api_timeout.log"], stdout=stdout)

    assert exit_code == 0
    report = json.loads(stdout.getvalue())
    assert report["findings"][0]["similar_incidents"] == []


def test_cli_history_add_list_and_analyze_with_database(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"

    add_stdout = io.StringIO()
    add_code = main(
        [
            "history",
            "add",
            "fixtures/api_timeout.log",
            "--db",
            str(db_path),
            "--resolution",
            "Confirmed upstream latency; timeout and retry policy were adjusted.",
        ],
        stdout=add_stdout,
    )
    assert add_code == 0
    stored = json.loads(add_stdout.getvalue())
    assert stored["stored_incident"]["incident_type"] == "external_api_timeout"

    list_stdout = io.StringIO()
    list_code = main(["history", "list", "--db", str(db_path)], stdout=list_stdout)
    assert list_code == 0
    history = json.loads(list_stdout.getvalue())
    assert stored["schema_version"] == REPORT_SCHEMA_VERSION
    assert history["schema_version"] == REPORT_SCHEMA_VERSION
    assert history["history_count"] == 1
    assert (
        history["incidents"][0]["resolution"] == "Confirmed upstream latency; timeout and retry policy were adjusted."
    )

    analyze_stdout = io.StringIO()
    analyze_code = main(
        ["analyze", "fixtures/api_timeout.log", "--db", str(db_path)],
        stdout=analyze_stdout,
    )
    assert analyze_code == 0
    report = json.loads(analyze_stdout.getvalue())
    similar = report["findings"][0]["similar_incidents"]
    assert similar[0]["incident_type"] == "external_api_timeout"
    assert similar[0]["similarity_score"] == 0.7


def test_cli_history_add_rejects_empty_resolution(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = main(
        [
            "history",
            "add",
            "fixtures/api_timeout.log",
            "--db",
            str(tmp_path / "incidents.db"),
            "--resolution",
            "   ",
        ],
        stderr=stderr,
    )

    assert exit_code == 2
    assert "resolution must not be empty" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_history_add_rejects_log_without_findings(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = main(
        [
            "history",
            "add",
            "fixtures/unknown_incident.log",
            "--db",
            str(tmp_path / "incidents.db"),
            "--resolution",
            "resolved",
        ],
        stderr=stderr,
    )

    assert exit_code == 2
    assert "no findings detected" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_history_add_requires_incident_type_for_mixed_log(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = main(
        [
            "history",
            "add",
            "fixtures/mixed.log",
            "--db",
            str(tmp_path / "incidents.db"),
            "--resolution",
            "resolved",
        ],
        stderr=stderr,
    )

    assert exit_code == 2
    assert "multiple findings detected" in stderr.getvalue()


def test_cli_history_add_selects_incident_type_for_mixed_log(tmp_path: Path) -> None:
    stdout = io.StringIO()

    exit_code = main(
        [
            "history",
            "add",
            "fixtures/mixed.log",
            "--db",
            str(tmp_path / "incidents.db"),
            "--resolution",
            "database resolved",
            "--incident-type",
            "database_connection_error",
        ],
        stdout=stdout,
    )

    assert exit_code == 0
    report = json.loads(stdout.getvalue())
    assert report["stored_incident"]["incident_type"] == "database_connection_error"


def test_cli_rejects_invalid_similar_limit() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["analyze", "fixtures/api_timeout.log", "--similar-limit", "21"])

    assert exc_info.value.code == 2


def test_cli_read_only_database_missing_has_no_traceback(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = main(
        ["history", "list", "--db", str(tmp_path / "missing.db")],
        stderr=stderr,
    )

    assert exit_code == 2
    assert "database file does not exist" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_analyze_bundle_default_glob() -> None:
    stdout = io.StringIO()

    exit_code = main(["analyze-bundle", "fixtures/bundle"], stdout=stdout)

    assert exit_code == 0
    report = json.loads(stdout.getvalue())
    assert report["schema_version"] == REPORT_SCHEMA_VERSION
    assert report["analysis_mode"] == "bundle"
    assert [source["source_name"] for source in report["sources"]] == [
        "auth.log",
        "backend.log",
        "gateway.log",
        "unrelated.log",
        "worker.log",
    ]


def test_cli_analyze_bundle_explicit_glob(tmp_path: Path) -> None:
    (tmp_path / "b.log").write_text("2026-07-15T00:00:00Z INFO b", encoding="utf-8")
    (tmp_path / "a.txt").write_text("2026-07-15T00:00:00Z INFO a", encoding="utf-8")
    stdout = io.StringIO()

    exit_code = main(["analyze-bundle", str(tmp_path), "--glob", "*.txt"], stdout=stdout)

    assert exit_code == 0
    assert json.loads(stdout.getvalue())["sources"] == [{"source_name": "a.txt", "line_count": 1}]


def test_cli_analyze_bundle_rejects_missing_directory() -> None:
    stderr = io.StringIO()

    exit_code = main(["analyze-bundle", "fixtures/missing-bundle"], stderr=stderr)

    assert exit_code == 2
    assert "bundle directory does not exist" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_analyze_bundle_rejects_empty_match(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = main(["analyze-bundle", str(tmp_path)], stderr=stderr)

    assert exit_code == 2
    assert "no files matched glob" in stderr.getvalue()


def test_cli_analyze_bundle_rejects_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "bad.log").write_bytes(b"\xff\xfe\xfd")
    stderr = io.StringIO()

    exit_code = main(["analyze-bundle", str(tmp_path)], stderr=stderr)

    assert exit_code == 2
    assert "codec can't decode" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_analyze_bundle_with_database(tmp_path: Path) -> None:
    db_path = tmp_path / "incidents.db"
    add_code = main(
        [
            "history",
            "add",
            "fixtures/api_timeout.log",
            "--db",
            str(db_path),
            "--resolution",
            "Provider latency confirmed.",
        ],
        stdout=io.StringIO(),
    )
    assert add_code == 0
    stdout = io.StringIO()

    exit_code = main(["analyze-bundle", "fixtures/bundle", "--db", str(db_path)], stdout=stdout)

    assert exit_code == 0
    findings = json.loads(stdout.getvalue())["findings"]
    timeout = [finding for finding in findings if finding["incident_type"] == "external_api_timeout"][0]
    similar = timeout["similar_incidents"]
    assert similar[0]["incident_type"] == "external_api_timeout"


def test_cli_analyze_bundle_invalid_similar_limit() -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["analyze-bundle", "fixtures/bundle", "--similar-limit", "0"])

    assert exc_info.value.code == 2


def test_console_entry_point_version() -> None:
    executable = shutil.which(
        "incident-triage",
        path=os.pathsep.join([str(Path(sys.executable).parent), *os.get_exec_path()]),
    )

    assert executable is not None

    result = subprocess.run(
        [executable, "--version"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert "0.6.0" in result.stdout


def test_triadapter_still_uses_same_cli() -> None:
    result = subprocess.run(
        [sys.executable, "triage.py", "fixtures/api_timeout.log"],
        check=False,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert result.returncode == 0
    assert json.loads(result.stdout)["schema_version"] == "0.4"


def test_cli_unsupported_schema_version_has_no_traceback(tmp_path: Path) -> None:
    db_path = tmp_path / "future.db"
    connection = sqlite3.connect(db_path)
    connection.execute("PRAGMA user_version = 999")
    connection.close()
    stderr = io.StringIO()

    exit_code = main(["history", "list", "--db", str(db_path)], stderr=stderr)

    assert exit_code == 2
    assert "unsupported SQLite schema version" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()


def test_cli_corrupt_sqlite_file_has_no_traceback(tmp_path: Path) -> None:
    db_path = tmp_path / "corrupt.db"
    db_path.write_text("not a sqlite database", encoding="utf-8")
    stderr = io.StringIO()

    exit_code = main(["history", "list", "--db", str(db_path)], stderr=stderr)

    assert exit_code == 2
    assert "file is not a database" in stderr.getvalue()
    assert "Traceback" not in stderr.getvalue()
