"""PII sentinel — Cloud DLP inspection of configured BigQuery tables.

For each configured target table, runs the DLP inspect API over a
sample of rows. Findings (info_type + likelihood) are written to
``governance.pii_findings`` (separate from ``gap_reports`` because DLP
findings have different retention semantics).

High-severity PII (direct identifiers: email, phone, SSN, payment
cards) are ALSO mirrored into gap_reports with scope='pii' so they
flow through the standard rollup + incident path.
"""

from .audit import AuditResult, PiiFinding, sweep
from .targets import PiiTarget, PiiTargetsConfig

__all__ = [
    "AuditResult",
    "PiiFinding",
    "PiiTarget",
    "PiiTargetsConfig",
    "sweep",
]
