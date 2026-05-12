"""Incident lifecycle management.

An incident groups one or more related findings + events under a
single workflow (or scope_id) across the period from first failure to
resolution. The incident table is the operator's "one row to glance
at" — it answers *what's broken right now* without making them scan
gap_reports.

Open rules (when to create a new incident):
  - A workflow has ``open_threshold`` consecutive critical findings.
  - A workflow's retry orchestrator marks it ``exhausted``.
  - Any single ``critical`` finding fires (configurable).

Close rules (when to set ``closed_at``):
  - All linked gap_reports rows now have ``resolved_at`` set.
  - The workflow returns to green in pipeline_health_v1 for
    ``close_quiet_hours`` hours.
"""

from .audit import AuditResult, open_incident, close_incident, sweep

__all__ = ["AuditResult", "close_incident", "open_incident", "sweep"]
