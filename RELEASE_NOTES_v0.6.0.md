# Incident Triage Copilot v0.6.0

Incident Triage Copilot is a deterministic Python backend for evidence-based incident triage from text logs. It analyzes log content, reports findings as JSON, and preserves exact evidence lines for every conclusion.

## Highlights

- Rule-based detection for external API timeouts, database connection failures, and authorization failures.
- Normalized log events with exact `source_name`, `line_number`, and raw line `text`.
- Request ID and time-window correlation.
- Multi-source bundle analysis.
- Optional SQLite history of resolved incidents.
- Deterministic similarity ranking for previously resolved incidents.
- FastAPI HTTP API and installable CLI entry point.
- Docker, Docker Compose, structured request logging, health and readiness endpoints.
- Ruff, mypy, branch coverage, and GitHub Actions CI.

## Architecture

- `incident_triage.parser` normalizes textual log lines.
- `incident_triage.rules` defines deterministic incident rules.
- `incident_triage.analyzer` builds evidence-based reports.
- `incident_triage.service` exposes shared application behavior for CLI and API.
- `incident_triage.storage` manages SQLite history and similarity lookup.
- `incident_triage.api` exposes FastAPI endpoints.
- `incident_triage.cli` powers both `incident-triage` and `python triage.py`.

The application version is `0.6.0`; the public report schema version is `0.4`; the API version is `1`; the SQLite schema version is `1`.

## Quick CLI Start

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"

incident-triage --version
incident-triage analyze fixtures/api_timeout.log
incident-triage analyze-bundle fixtures/bundle
```

## Docker Compose

```powershell
docker compose up --build
```

The API listens on `http://127.0.0.1:8000`. SQLite history is stored in the Compose volume mounted at `/data`.

## Example API Request

```powershell
$body = @{
  source_name = "api_timeout.log"
  content = Get-Content fixtures/api_timeout.log -Raw
} | ConvertTo-Json

Invoke-RestMethod `
  -Method POST `
  -Uri http://127.0.0.1:8000/v1/analyze `
  -ContentType "application/json" `
  -Headers @{ "X-Request-ID" = "demo-1" } `
  -Body $body
```

## Validation Summary

Release candidate validation for `v0.6.0` passed:

- Ruff check: passed.
- Ruff format check: passed.
- Mypy: passed.
- Pytest with `-W error`: passed with zero warnings.
- Branch coverage: above the required 90% threshold.
- Docker build and Compose smoke test: passed.

## Limitations

- Deterministic rule-based detection only.
- Three built-in incident types.
- SQLite only.
- No authentication.
- No LLM.
- No recursive bundle scanning.
- No automatic deployment.

## Why This Project Matters

This release demonstrates practical Python backend engineering for Support L2/L3 workflows: reproducible incident triage, exact evidence handling, service/API separation, local persistence, CI quality gates, and deployment-ready container behavior. It is AI-ready automation in the sense that evidence and contracts are structured for future automation, but this version does not use AI or LLMs.
