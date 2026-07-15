# Incident Triage Copilot v0.6.1

This is a patch release for Incident Triage Copilot. It contains documentation updates and cross-platform test fixes only.

## Fixed

- Made the installed `incident-triage` console entry-point test portable across Windows and Linux.
- Removed platform-dependent absolute fixture paths from CLI and service tests.
- Restored the GitHub Actions pipeline to green on Ubuntu.

## Documentation

- Added a complete README table of contents.
- Added return navigation links to README sections.
- Corrected runtime and development requirements.
- Updated the roadmap so it does not list already implemented functionality as future work.

## Compatibility

No production runtime behavior changed in this release.

No API contract, report schema or SQLite schema changed:

- report schema version: `0.4`,
- API version: `1`,
- SQLite schema version: `1`.

## Validation

Release validation passed with:

- Ruff check,
- Ruff format check,
- mypy,
- pytest with warnings treated as errors,
- branch coverage above the required 90% threshold,
- Docker build,
- Docker Compose smoke test.

Local test result:

```text
96 passed
Total coverage: 92.35%
Warnings: 0
```

## CLI

Run a single log analysis:

```powershell
incident-triage analyze fixtures/api_timeout.log
```

Check the installed version:

```powershell
incident-triage --version
```

Expected version:

```text
incident-triage 0.6.1
```

## Docker Compose

Start the API service:

```powershell
docker compose up -d --build
```

Stop and remove the test volume:

```powershell
docker compose down --volumes --remove-orphans
```

## Changelog

See [CHANGELOG.md](CHANGELOG.md) for the full changelog.
