"""Validación estadística y checks anti-bug."""

from .calibration import (
    CalibrationBin,
    CalibrationReport,
    brier_score,
    calibration_curve,
    evaluate,
    expected_calibration_error,
    log_loss,
)
from .features import (
    CollinearPair,
    FeatureDiagnostics,
    SignFlip,
    correlation,
    diagnose,
    find_collinear_pairs,
    find_sign_flips,
)
from .sanity import BacktestSample, SanityFinding, SanityReport, Severity, run_sanity_checks

__all__ = [
    "BacktestSample",
    "CalibrationBin",
    "CalibrationReport",
    "CollinearPair",
    "FeatureDiagnostics",
    "SanityFinding",
    "SanityReport",
    "Severity",
    "SignFlip",
    "brier_score",
    "calibration_curve",
    "correlation",
    "diagnose",
    "evaluate",
    "expected_calibration_error",
    "find_collinear_pairs",
    "find_sign_flips",
    "log_loss",
    "run_sanity_checks",
]
