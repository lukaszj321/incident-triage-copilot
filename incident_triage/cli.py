from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

from incident_triage import __version__
from incident_triage.models import LogSource
from incident_triage.service import (
    ServiceError,
    analyze_log_file,
    analyze_sources,
    list_incident_history,
    store_resolved_incident_from_file,
    stored_incident_response,
)
from incident_triage.storage import StorageError

COMMANDS = {"analyze", "analyze-bundle", "history"}


class CliError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=program_name(),
        description="Analyze incident logs and manage local incident history.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    analyze_parser = subparsers.add_parser("analyze", help="Analyze a log file.")
    analyze_parser.add_argument("log_path", help="Path to a text log file.")
    analyze_parser.add_argument("--db", help="SQLite history database used for similar incident lookup.")
    analyze_parser.add_argument("--similar-limit", type=parse_similar_limit, default=3)
    analyze_parser.set_defaults(handler=handle_analyze)

    bundle_parser = subparsers.add_parser("analyze-bundle", help="Analyze a directory of log files.")
    bundle_parser.add_argument("bundle_path", help="Directory with log files.")
    bundle_parser.add_argument("--glob", default="*.log", help="File glob, non-recursive. Default: *.log")
    bundle_parser.add_argument("--db", help="SQLite history database used for similar incident lookup.")
    bundle_parser.add_argument("--similar-limit", type=parse_similar_limit, default=3)
    bundle_parser.set_defaults(handler=handle_analyze_bundle)

    history_parser = subparsers.add_parser("history", help="Manage resolved incident history.")
    history_subparsers = history_parser.add_subparsers(dest="history_command", required=True)

    add_parser = history_subparsers.add_parser("add", help="Add a resolved incident to history.")
    add_parser.add_argument("log_path", help="Path to a text log file.")
    add_parser.add_argument("--db", required=True, help="SQLite history database.")
    add_parser.add_argument("--resolution", required=True, help="Resolution text for the stored incident.")
    add_parser.add_argument("--incident-type", help="Incident type to store when the log has multiple findings.")
    add_parser.set_defaults(handler=handle_history_add)

    list_parser = history_subparsers.add_parser("list", help="List stored incident history.")
    list_parser.add_argument("--db", required=True, help="SQLite history database.")
    list_parser.set_defaults(handler=handle_history_list)
    return parser


def main(argv: Sequence[str] | None = None, stdout: TextIO | None = None, stderr: TextIO | None = None) -> int:
    output = stdout or sys.stdout
    errors = stderr or sys.stderr
    configure_utf8(output)
    configure_utf8(errors)
    parser = build_parser()

    try:
        args = parser.parse_args(normalize_legacy_args(argv))
        result = args.handler(args)
    except (CliError, ServiceError, StorageError, sqlite3.DatabaseError, UnicodeDecodeError) as exc:
        print(f"error: {exc}", file=errors)
        return 2

    json.dump(result, output, ensure_ascii=False, indent=2)
    print(file=output)
    return 0


def normalize_legacy_args(argv: Sequence[str] | None) -> list[str] | None:
    if argv is None:
        argv = sys.argv[1:]
    args = list(argv)
    if args and args[0] not in COMMANDS and args[0] not in {"-h", "--help", "--version"}:
        return ["analyze", *args]
    return args


def handle_analyze(args: argparse.Namespace) -> dict[str, object]:
    return run_analyze(
        Path(args.log_path), db_path=Path(args.db) if args.db else None, similar_limit=args.similar_limit
    )


def handle_analyze_bundle(args: argparse.Namespace) -> dict[str, object]:
    bundle_path = Path(args.bundle_path)
    if not bundle_path.exists():
        raise CliError(f"bundle directory does not exist: {bundle_path}")
    if not bundle_path.is_dir():
        raise CliError(f"bundle path is not a directory: {bundle_path}")

    files = [path for path in bundle_path.glob(args.glob) if path.is_file()]
    files = sorted(files, key=lambda path: path.relative_to(bundle_path).as_posix())
    if not files:
        raise CliError(f"no files matched glob {args.glob!r} in {bundle_path}")

    sources = [
        LogSource(
            source_name=path.relative_to(bundle_path).as_posix(),
            content=path.read_text(encoding="utf-8"),
        )
        for path in files
    ]
    return analyze_sources(
        sources,
        db_path=Path(args.db) if args.db else None,
        similar_limit=args.similar_limit,
    )


def run_analyze(log_path: Path, db_path: Path | None = None, similar_limit: int = 3) -> dict[str, object]:
    validate_log_path(log_path)
    return analyze_log_file(log_path, db_path=db_path, similar_limit=similar_limit)


def handle_history_add(args: argparse.Namespace) -> dict[str, object]:
    validate_log_path(Path(args.log_path))
    stored = store_resolved_incident_from_file(
        Path(args.log_path),
        db_path=Path(args.db),
        resolution=args.resolution,
        incident_type=args.incident_type,
    )
    return stored_incident_response(stored)


def handle_history_list(args: argparse.Namespace) -> dict[str, object]:
    return list_incident_history(Path(args.db))


def validate_log_path(log_path: Path) -> None:
    if not log_path.exists():
        raise CliError(f"log file does not exist: {log_path}")
    if not log_path.is_file():
        raise CliError(f"log path is not a file: {log_path}")


def parse_similar_limit(value: str) -> int:
    try:
        limit = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--similar-limit must be an integer from 1 to 20") from exc
    if limit < 1 or limit > 20:
        raise argparse.ArgumentTypeError("--similar-limit must be in range 1-20")
    return limit


def configure_utf8(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        reconfigure(encoding="utf-8")


def program_name() -> str:
    if Path(sys.argv[0]).stem == "incident-triage":
        return "incident-triage"
    return "python triage.py"
