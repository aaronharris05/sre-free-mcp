"""Cross-cutting data models.

A ``Finding`` is one row destined for the ``governance.gap_reports``
table. Anomaly detection emits findings with ``scope='data'``; future
audit bots can emit findings with other scopes against the same table.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Finding:
    """One row for the gap_reports table.

    Attributes:
        scope: high-level category — ``'data'`` for anomaly findings.
            Future audit bots can extend with ``'cost'``, ``'security'``, etc.
        scope_id: identifier within the scope. For anomaly findings the
            convention is ``"{table}:{metric_column}"``.
        gap_kind: machine-readable kind discriminator —
            ``'anomaly_zscore'`` for anomaly findings.
        severity: ``'low'`` | ``'medium'`` | ``'high'`` | ``'critical'``.
        details: free-form JSON-serializable bag — owner_team, z-score,
            value, as_of timestamp, model name, etc.
    """

    scope: str
    scope_id: str
    gap_kind: str
    severity: str
    details: dict[str, Any]


__all__ = ["Finding"]
