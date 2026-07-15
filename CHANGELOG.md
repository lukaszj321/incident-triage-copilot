# Changelog

## [0.6.1] - 2026-07-15

### Fixed

- Made the installed console entry-point test portable across Windows and Linux.
- Removed platform-dependent absolute fixture paths from CLI and service tests.
- Restored a fully green GitHub Actions pipeline on Ubuntu.

### Documentation

- Added a complete README table of contents and return navigation links.
- Corrected runtime and development requirements.
- Updated the roadmap to exclude already implemented functionality.

No production runtime behavior, API contract, report schema or database schema changed in this release.

## [0.6.0] - 2026-07-15

### Added

- Evidence-based incident detection.
- Normalization of log events.
- Request ID and time-window correlation.
- Multi-source bundle analysis.
- SQLite incident history.
- Deterministic similarity ranking.
- FastAPI HTTP API.
- CLI package entry point.
- Docker and Docker Compose.
- Structured request logging.
- Health and readiness endpoints.
- Ruff, mypy, coverage and GitHub Actions CI.

### Security

- Evidence must reference exact log lines.
- API does not read client filesystem paths.
- Application runs as non-root in Docker.
- Request bodies and log contents are not written to application logs.

### Known limitations

- Deterministic rule-based detection only.
- Three built-in incident types.
- SQLite only.
- No authentication.
- No LLM.
- No recursive bundle scanning.
