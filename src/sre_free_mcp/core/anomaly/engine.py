"""Slim anomaly-detection engine — IsolationForest with z-equivalent scores.

A self-contained, dependency-light alternative to the full leaderboard
in ``qorik-ds-agent`` (IF / LOF / OCSVM / EE / PCA). One detector,
sensible defaults, and a uniform "higher score = more anomalous"
convention so the orchestrator can apply a single z-threshold across
all targets.

Adding LOF or other detectors here is straightforward — wrap them in
:func:`_fit_one`, append to the candidate list in :func:`score`, and
pick by a simple criterion (variance, mean score above contamination
quantile, etc.). Kept to one detector for v1 to keep the code path
auditable.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Minimum window size before a detector is fit. Below this the fit
# isn't statistically meaningful — return empty scores and let the
# orchestrator log a "thin window" message.
_MIN_FIT_WINDOW = 30

# Default contamination prior. 5% is a reasonable starting point for
# clean operational data; the orchestrator's z-threshold further filters.
_DEFAULT_CONTAMINATION = 0.05


def score(
    values: list[float],
    *,
    contamination: float = _DEFAULT_CONTAMINATION,
) -> tuple[list[float], str]:
    """Return per-row anomaly scores (higher = more anomalous) and the
    model name used.

    Returns ``([], "insufficient_data")`` if the input is too small to
    fit a detector reliably.
    """
    if len(values) < _MIN_FIT_WINDOW:
        return [], "insufficient_data"

    # Lazy import — sklearn pulls in a lot of code, so unit tests that
    # patch :func:`score` don't pay the import cost.
    import numpy as np
    from sklearn.ensemble import IsolationForest
    from sklearn.preprocessing import StandardScaler

    arr = np.asarray(values, dtype=float).reshape(-1, 1)
    arr = StandardScaler().fit_transform(arr)

    model = IsolationForest(
        contamination=contamination,
        random_state=42,
        n_estimators=100,
    )
    model.fit(arr)
    # sklearn convention: lower score_samples = more anomalous. Flip so
    # higher = more anomalous, matching z-equivalent orientation.
    raw_scores = (-model.score_samples(arr)).tolist()
    return raw_scores, "isolation_forest"


def z_equivalent(scores: list[float]) -> list[float]:
    """Convert raw anomaly scores to z-equivalents.

    Different detectors return scores on different scales (decision
    function, log-density, reconstruction error). Standardizing against
    the score distribution gives the orchestrator a uniform threshold
    knob across detectors.
    """
    if not scores:
        return []
    n = len(scores)
    mean = sum(scores) / n
    var = sum((x - mean) ** 2 for x in scores) / n
    std = var**0.5
    if std == 0:
        return [0.0] * n
    return [(x - mean) / std for x in scores]


__all__ = ["score", "z_equivalent"]


# Internal helper present for future expansion (LOF, etc.).
def _fit_one(name: str, values: Any, contamination: float) -> tuple[str, list[float]] | None:
    """Placeholder for multi-detector expansion. Returns None for now."""
    return None
