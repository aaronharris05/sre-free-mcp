"""Weekly rollup — cross-bot summary of unresolved findings + open incidents.

The operator's Monday-morning report. Aggregates every active audit
bot's output into one email:

  - Count of open findings by scope and severity
  - Count of open incidents
  - Top-5 worst findings (by severity, then recency)
  - Pending approval_queue items needing human review

Optionally promotes high-severity unresolved findings into the
approval_queue so they don't get lost. The rollup bot itself is
detection-only — it never mutates gap_reports or incidents.
"""

from .audit import AuditResult, RollupSummary, sweep
from .email import build_bodies, build_subject

__all__ = [
    "AuditResult",
    "RollupSummary",
    "build_bodies",
    "build_subject",
    "sweep",
]
