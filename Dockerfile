FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    INCIDENT_TRIAGE_DB=/data/incidents.db

WORKDIR /app

RUN adduser --disabled-password --gecos "" --home /home/appuser appuser \
    && mkdir -p /data \
    && chown appuser:appuser /data

COPY pyproject.toml README.md triage.py ./
COPY incident_triage ./incident_triage
COPY fixtures ./fixtures

RUN python -m pip install --no-cache-dir . \
    && chmod -R a-w /app

USER appuser
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 CMD python -c "from urllib.request import urlopen; import sys; sys.exit(0 if urlopen('http://127.0.0.1:8000/health', timeout=2).status == 200 else 1)"

CMD ["uvicorn", "incident_triage.api:app", "--host", "0.0.0.0", "--port", "8000"]
