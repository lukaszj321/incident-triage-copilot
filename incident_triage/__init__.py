"""Incident Triage Copilot."""

from importlib.metadata import PackageNotFoundError, version

from incident_triage.analyzer import analyze_log

try:
    __version__ = version("incident-triage-copilot")
except PackageNotFoundError:
    __version__ = "0.6.1"

__all__ = ["__version__", "analyze_log"]
