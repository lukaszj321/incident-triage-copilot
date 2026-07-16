# Incident Triage Copilot

[![CI](https://github.com/lukaszj321/incident-triage-copilot/actions/workflows/ci.yml/badge.svg)](https://github.com/lukaszj321/incident-triage-copilot/actions/workflows/ci.yml)

Aktualna wersja: `0.6.1`

Release: <https://github.com/lukaszj321/incident-triage-copilot/releases/tag/v0.6.1>

Incident Triage Copilot zamienia tekstowe logi aplikacyjne w uporządkowany raport wstępnej diagnozy incydentu.

Narzędzie koreluje powiązane wpisy po `request_id` lub czasie, rozpoznaje obsługiwane typy awarii, pokazuje dokładne linie logu będące dowodem i proponuje kolejne kroki diagnostyczne.

Nie zastępuje analizy inżyniera i nie wykonuje automatycznej naprawy. Automatyzuje pierwszy, powtarzalny etap triage'u.

Najważniejsza zasada: każdy wniosek musi mieć dowody w konkretnych, niezmodyfikowanych liniach logu. Aplikacja nie zgaduje przyczyny bez evidence.

Projekt nie używa LLM, embeddingów, PostgreSQL, SQLAlchemy, frontendu ani uwierzytelniania.

## Spis treści

- [Jak to działa w praktyce](#jak-to-działa-w-praktyce)
- [Cel MVP](#cel-mvp)
- [Aktualny zakres](#aktualny-zakres)
- [Wymagania](#wymagania)
- [Instalacja lokalna](#instalacja-lokalna)
- [CLI](#cli)
- [API](#api)
- [Request ID i logowanie](#request-id-i-logowanie)
- [Kontrakt raportu JSON](#kontrakt-raportu-json)
- [Fixture'y](#fixturey)
- [SQLite](#sqlite)
- [Quality gates](#quality-gates)
- [Docker](#docker)
- [Docker Compose](#docker-compose)
- [CI](#ci)
- [Struktura projektu](#struktura-projektu)
- [Roadmap](#roadmap)
- [Ograniczenia](#ograniczenia)

## Jak to działa w praktyce

### Przepływ analizy

```mermaid
flowchart LR
    A[CLI lub FastAPI] --> B[Logi tekstowe]
    B --> C[Parser i normalizacja]
    C --> D[Korelacja request_id lub okno 30 s]
    D --> E[Deterministyczne reguły detekcji]
    E --> F[Finding z evidence i context]
    F --> G[Raport JSON]
    H[(Historia SQLite)] --> I[Ranking podobnych incydentów]
    F --> I
    I --> G
```

Podstawowa analiza działa bez bazy danych. SQLite jest używane tylko do przechowywania rozwiązanych incydentów i wyszukiwania podobnych przypadków.

### Przykładowy incydent

Klient próbuje wykonać płatność, ale aplikacja zwraca błąd. Operator lub inżynier supportu otrzymuje log i chce szybko sprawdzić, czy problem dotyczy aplikacji, bazy danych, autoryzacji czy zewnętrznego API. Incident Triage Copilot pomaga przygotować wstępną diagnozę na podstawie dostępnych wpisów, ale nie jest kompletnym systemem produkcyjnym ani narzędziem automatycznej naprawy.

### Log wejściowy

```text
2026-07-15T09:14:02Z INFO request_id=abc-1 user=zażółć start checkout submit
2026-07-15T09:14:05Z ERROR request_id=abc-1 upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge
2026-07-15T09:14:05Z INFO request_id=abc-1 response_status=502
```

### Uruchomienie

```powershell
incident-triage analyze fixtures/api_timeout.log
```

### Najważniejsza część wyniku

Poniżej znajduje się skrócony fragment rzeczywistej odpowiedzi CLI.

```json
{
  "status": "incidents_detected",
  "findings": [
    {
      "incident_type": "external_api_timeout",
      "symptom": "Timeout while calling an external API or upstream HTTP service.",
      "probable_cause": "The external API or upstream HTTP service did not respond before the configured timeout.",
      "confidence": 0.9,
      "correlation": {
        "strategy": "request_id",
        "key": "abc-1",
        "window_seconds": null,
        "source_count": 1
      },
      "evidence": [
        {
          "source_name": "fixtures/api_timeout.log",
          "line_number": 2,
          "text": "2026-07-15T09:14:05Z ERROR request_id=abc-1 upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge"
        }
      ],
      "context": [
        {
          "source_name": "fixtures/api_timeout.log",
          "line_number": 1,
          "text": "2026-07-15T09:14:02Z INFO request_id=abc-1 user=zażółć start checkout submit"
        },
        {
          "source_name": "fixtures/api_timeout.log",
          "line_number": 3,
          "text": "2026-07-15T09:14:05Z INFO request_id=abc-1 response_status=502"
        }
      ],
      "recommended_actions": [
        "Check the referenced upstream API endpoint and provider status.",
        "Inspect client timeout settings and retry behavior for the affected request.",
        "Correlate this line with surrounding request identifiers before changing production settings."
      ]
    }
  ]
}
```

### Co aplikacja ustaliła

- Wykryto timeout zewnętrznego API.
- Powiązane wpisy połączono przez `request_id=abc-1`.
- Dokładna linia timeoutu została wskazana jako `evidence`.
- Status HTTP `502` został dołączony jako `context`.
- Aplikacja zaproponowała kolejne kroki diagnostyczne; jest to wstępna diagnoza, czyli najbardziej prawdopodobne wyjaśnienie według reguły.

### Co to daje operatorowi

Bez narzędzia operator musiałby ręcznie przeszukać log, znaleźć powiązane wpisy, połączyć je po identyfikatorze requestu, sklasyfikować typ awarii i przygotować podstawowe podsumowanie incydentu. Z narzędziem otrzymuje od razu uporządkowane podsumowanie, klasyfikację problemu, dokładne linie dowodowe, kontekst requestu i listę kolejnych czynności diagnostycznych.

logi -> normalizacja -> korelacja -> wykrycie reguły -> raport z dowodami

[↑ Powrót do spisu treści](#spis-treści)

---

## Cel MVP

MVP analizuje niestrukturyzowane logi tekstowe, rozpoznaje obsługiwane scenariusze incydentów, łączy zdarzenia w wielu źródłach przez `request_id` lub okno czasowe i zwraca raport JSON. Historia rozwiązanych incydentów jest opcjonalna i zapisywana lokalnie w SQLite.

[↑ Powrót do spisu treści](#spis-treści)

---

## Aktualny zakres

- CLI: `python triage.py ...` oraz instalowalna komenda `incident-triage`.
- API HTTP: FastAPI z `/health`, `/ready`, `/v1/analyze`, `/v1/analyze-bundle`, `/v1/history`.
- Analiza pojedynczego logu i paczki plików `.log`.
- SQLite dla historii rozwiązanych incydentów i podobnych incydentów.
- Strukturalne logowanie requestów API jako pojedyncze rekordy JSON na stdout.
- Middleware `X-Request-ID`.
- Dockerfile, Docker Compose i workflow GitHub Actions.

Obsługiwane scenariusze wykrywania:

- timeout zewnętrznego API,
- błąd połączenia z bazą,
- nieudana autoryzacja.

[↑ Powrót do spisu treści](#spis-treści)

---

## Wymagania

### Runtime

- Python 3.12 lub nowszy,
- zależności instalowane automatycznie z `pyproject.toml`,
- opcjonalnie Docker i Docker Compose.

### Development

- zależności z grupy `.[dev]`,
- pytest,
- pytest-cov,
- Ruff,
- mypy,
- build,
- httpx2.

`pyproject.toml` pozostaje źródłem prawdy dla zależności i ich wersji.

[↑ Powrót do spisu treści](#spis-treści)

---

## Instalacja lokalna

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Instalacja bez zależności developerskich:

```powershell
python -m pip install -e .
```

[↑ Powrót do spisu treści](#spis-treści)

---

## CLI

```powershell
python triage.py fixtures/api_timeout.log
python triage.py analyze fixtures/api_timeout.log
python triage.py analyze-bundle fixtures/bundle
python triage.py history list --db data/incidents.db
```

Po instalacji pakietu działa ten sam CLI jako entry point:

```powershell
incident-triage fixtures/api_timeout.log
incident-triage analyze fixtures/api_timeout.log
incident-triage analyze-bundle fixtures/bundle
incident-triage history list --db data/incidents.db
incident-triage --version
```

`triage.py` pozostaje cienkim adapterem do `incident_triage.cli:main`; logika CLI nie jest duplikowana.

[↑ Powrót do spisu treści](#spis-treści)

---

## API

Uruchomienie lokalne bez historii:

```powershell
.\.venv\Scripts\python.exe -m uvicorn incident_triage.api:app --reload
```

Uruchomienie z historią SQLite:

```powershell
$env:INCIDENT_TRIAGE_DB = "data/incidents.db"
.\.venv\Scripts\python.exe -m uvicorn incident_triage.api:app --reload
```

Endpointy:

- `GET /health` - sprawdza tylko proces aplikacji i nie tworzy bazy,
- `GET /ready` - sprawdza gotowość storage, jeżeli historia jest skonfigurowana,
- `POST /v1/analyze`,
- `POST /v1/analyze-bundle`,
- `POST /v1/history`,
- `GET /v1/history`,
- `GET /v1/history/{incident_id}`.

`/health` zwraca `service_version`, która pochodzi z centralnej wersji aplikacji `0.6.1`.

Projekt rozróżnia cztery wersje:

- wersja aplikacji: `0.6.1`,
- publiczny `schema_version` raportów i historii: `0.4`,
- `api_version`: `1`,
- wersja schematu SQLite: `1`.

OpenAPI jest dostępne pod:

- `/docs`,
- `/openapi.json`.

[↑ Powrót do spisu treści](#spis-treści)

---

## Request ID i logowanie

API akceptuje opcjonalny nagłówek `X-Request-ID`. Poprawna wartość ma 1-128 znaków i może zawierać litery, cyfry, `-`, `_`, `.`. Brak lub niepoprawna wartość jest zastępowana UUID. Odpowiedź zawsze zawiera `X-Request-ID`.

Kontrolowane błędy API zawierają `request_id`:

```json
{
  "error": {
    "code": "invalid_request",
    "message": "Invalid request.",
    "details": null,
    "request_id": "..."
  }
}
```

Każdy request API zapisuje jeden rekord JSON na stdout:

```json
{
  "duration_ms": 1.23,
  "event": "http_request_completed",
  "method": "POST",
  "path": "/v1/analyze",
  "request_id": "demo-1",
  "status_code": 200
}
```

Log requestu nie zawiera body, evidence, context, tokenów ani surowych błędów SQLite.

[↑ Powrót do spisu treści](#spis-treści)

---

## Kontrakt raportu JSON

Aktualny `schema_version` publicznych odpowiedzi analizy i historii to `0.4`.

```json
{
  "schema_version": "0.4",
  "analysis_mode": "single",
  "sources": [
    {
      "source_name": "fixtures/api_timeout.log",
      "line_count": 4
    }
  ],
  "status": "incidents_detected",
  "summary": {
    "source_count": 1,
    "findings_count": 1,
    "incident_types": ["external_api_timeout"]
  },
  "findings": [
    {
      "incident_type": "external_api_timeout",
      "symptom": "Timeout while calling an external API or upstream HTTP service.",
      "probable_cause": "The external API or upstream HTTP service did not respond before the configured timeout.",
      "evidence": [
        {
          "source_name": "fixtures/api_timeout.log",
          "line_number": 2,
          "text": "2026-07-15T09:14:05Z ERROR request_id=abc-1 upstream payment API timed out after 3000ms endpoint=https://payments.example.test/v1/charge"
        }
      ],
      "context": [],
      "recommended_actions": [
        "Check the referenced upstream API endpoint and provider status."
      ],
      "confidence": 0.9,
      "correlation": {
        "strategy": "request_id",
        "key": "abc-1",
        "window_seconds": null,
        "source_count": 1
      },
      "similar_incidents": []
    }
  ]
}
```

Każdy element `evidence` i `context` zawiera:

```json
{
  "source_name": "worker.log",
  "line_number": 1,
  "text": "dokładna, niezmodyfikowana linia logu"
}
```

Brak rozpoznanego incydentu zwraca `status: "no_incident_detected"`, puste `findings` i nie wymyśla przyczyny ani dowodów.

[↑ Powrót do spisu treści](#spis-treści)

---

## Fixture'y

Przykłady użytkowe są w `fixtures/`:

- `api_timeout.log`,
- `database_connection_error.log`,
- `authorization_failure.log`,
- `mixed.log`,
- `unknown_incident.log`,
- `bundle/` z wieloma źródłami do analizy paczki.

Testy korzystają z tych samych plików.

[↑ Powrót do spisu treści](#spis-treści)

---

## SQLite

Historia jest opcjonalna. Po ustawieniu `INCIDENT_TRIAGE_DB` lub `--db` aplikacja używa lokalnej bazy SQLite. Połączenia włączają `foreign_keys`, `busy_timeout`, `row_factory`, a zapisywalna baza jest inicjalizowana w trybie WAL. Nie ma globalnego połączenia.

API inicjalizuje schemat SQLite podczas kontrolowanego startupu FastAPI, jeżeli historia jest skonfigurowana. Inicjalizacja jest idempotentna i używa tej samej warstwy storage co zapis historii. Import modułu API, generowanie OpenAPI oraz `GET /health` nie tworzą bazy. `GET /ready` pozostaje operacją tylko do odczytu.

Jeżeli historia nie jest skonfigurowana, aplikacja nie tworzy domyślnej bazy: `/health` i `/ready` zwracają `history_storage = "disabled"`, analiza działa, a endpointy historii zwracają kontrolowane `503`.

Jeżeli historia jest skonfigurowana na świeżym volume, startup tworzy plik bazy i schemat. Wtedy `/ready` zwraca `200` oraz `{"status": "ready", "history_storage": "available"}`, a `GET /v1/history` zwraca pustą historię.

Błędna konfiguracja storage, na przykład uszkodzony plik SQLite, nieobsługiwana wersja schematu albo brak możliwości utworzenia katalogu bazy, powoduje fail-fast podczas startupu aplikacji. Publiczne odpowiedzi nie ujawniają pełnej ścieżki bazy, surowego błędu SQLite ani tracebacka.

W kontenerze baza jest pod `/data/incidents.db`; trwałość zapewnia volume.

[↑ Powrót do spisu treści](#spis-treści)

---

## Quality gates

```powershell
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
.\.venv\Scripts\python.exe -m mypy incident_triage
.\.venv\Scripts\python.exe -m pytest -W error `
  --cov=incident_triage `
  --cov-branch `
  --cov-report=term-missing `
  --cov-fail-under=90
```

Zależności developerskie obejmują `pytest`, `pytest-cov`, `ruff`, `mypy`, `build` i `httpx2`.

[↑ Powrót do spisu treści](#spis-treści)

---

## Docker

Budowa obrazu:

```powershell
docker build -t incident-triage-copilot:local .
```

Uruchomienie:

```powershell
docker run --rm -p 8000:8000 -v incident-triage-data:/data incident-triage-copilot:local
```

Obraz używa oficjalnego Python 3.12 slim, instaluje projekt jako pakiet, nie instaluje zależności developerskich, uruchamia `uvicorn` bez `--reload`, działa jako użytkownik non-root i wystawia port `8000`.

[↑ Powrót do spisu treści](#spis-treści)

---

## Docker Compose

```powershell
docker compose up --build
```

`compose.yaml` zawiera jeden serwis API i named volume `incident-triage-data` zamontowany pod `/data`.

[↑ Powrót do spisu treści](#spis-treści)

---

## CI

`.github/workflows/ci.yml` uruchamia się dla `push` i `pull_request`. Pipeline wykonuje instalację z zależnościami developerskimi, Ruff, format check, mypy, pytest z branch coverage i progiem 90%, a następnie buduje obraz Docker. Workflow nie publikuje obrazu i nie wymaga sekretów.

[↑ Powrót do spisu treści](#spis-treści)

---

## Struktura projektu

```text
.
|-- .dockerignore
|-- .github/
|   `-- workflows/
|       `-- ci.yml
|-- Dockerfile
|-- README.md
|-- compose.yaml
|-- fixtures/
|   |-- bundle/
|   |-- api_timeout.log
|   |-- authorization_failure.log
|   |-- database_connection_error.log
|   |-- mixed.log
|   `-- unknown_incident.log
|-- incident_triage/
|   |-- __init__.py
|   |-- analyzer.py
|   |-- api.py
|   |-- cli.py
|   |-- models.py
|   |-- parser.py
|   |-- rules.py
|   |-- service.py
|   |-- similarity.py
|   |-- storage.py
|   `-- versions.py
|-- pyproject.toml
|-- tests/
|   |-- test_analyzer.py
|   |-- test_api.py
|   |-- test_cli.py
|   |-- test_parser.py
|   |-- test_service.py
|   `-- test_storage.py
`-- triage.py
```

[↑ Powrót do spisu treści](#spis-treści)

---

## Roadmap

- więcej formatów i reguł normalizacji logów,
- rozbudowane reguły korelacji,
- opcjonalny backend PostgreSQL,
- uwierzytelnianie API,
- opcjonalna warstwa LLM działająca wyłącznie na evidence,
- obserwowalność przez OpenTelemetry lub Prometheus.

Roadmapa opisuje przyszłe kierunki, nie funkcje ukończone ani konkretne wersje.

[↑ Powrót do spisu treści](#spis-treści)

---

## Ograniczenia

- Brak LLM i embeddingów.
- Brak PostgreSQL.
- Brak SQLAlchemy.
- Brak frontendu.
- Brak uwierzytelniania.
- Brak Redis, Celery, Kubernetes, reverse proxy i TLS.
- Brak zewnętrznego systemu logowania, Prometheusa i OpenTelemetry.
- Brak automatycznego deploymentu.
- Brak multipart upload.
- Brak streamingu i obserwowania katalogu.
- Brak rekurencyjnego skanowania bundle.
- Brak zapisu całej paczki bundle do historii jednym poleceniem.

[↑ Powrót do spisu treści](#spis-treści)
