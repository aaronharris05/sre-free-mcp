"""Data catalog audit — verify the workflows registry is structurally complete.

Each row in ``governance.workflows`` is the customer's promise to the
rest of the SRE fleet (retry, freshness, cost) about how this workflow
behaves. Missing required fields mean downstream bots can't do their
jobs.

Rules implemented in :mod:`checks`:
  - ``missing_source_path`` — no source_path means SCM audit + RCA
    bot can't link to source code.
  - ``missing_owner_team`` — anomaly + finding emails won't route.
  - ``missing_cron_for_scheduler`` — trigger_kind=scheduler implies a
    cron string; absent cron breaks job_uptime missed-run rule.
  - ``unparseable_cron`` — cron present but doesn't match a recognised
    pattern; same downstream consequence.
  - ``missing_idempotent_flag`` — NULL idempotent forces retry
    orchestrator to veto every retry; explicit FALSE is the safer
    declaration.
"""

from .audit import AuditResult, sweep
from .checks import evaluate

__all__ = ["AuditResult", "evaluate", "sweep"]
