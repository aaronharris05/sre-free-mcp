"""Freshness audit — detect tables that aren't being updated on their
expected cadence.

Three rules implemented in :mod:`checks`:
  - ``table_stale``       — age > 2x (high) or 5x (critical) expected cadence
  - ``table_empty``       — row_count = 0 for >24h after last write
  - ``ingest_at_lagging`` — explicit ``MAX(ingest_at)`` lag check (for
    tables that carry the ingest_at convention)
"""

from .audit import AuditResult, sweep
from .checks import TableFreshness, evaluate
from .targets import FreshnessTarget, FreshnessTargetsConfig

__all__ = [
    "AuditResult",
    "FreshnessTarget",
    "FreshnessTargetsConfig",
    "TableFreshness",
    "evaluate",
    "sweep",
]
