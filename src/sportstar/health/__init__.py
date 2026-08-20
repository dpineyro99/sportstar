"""Calidad de datos: detectar el fallo silencioso antes de que contamine."""

from .checks import Finding
from .runner import ALL_CHECKS, HealthReport, persist_report, run_checks

__all__ = ["ALL_CHECKS", "Finding", "HealthReport", "persist_report", "run_checks"]
