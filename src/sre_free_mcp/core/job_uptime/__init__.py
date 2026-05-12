"""Job uptime audit — detect workflows that should have run but didn't,
and workflows that keep failing.

Five rules implemented in :mod:`checks`:
  1. workflow_paused
  2. workflow_missed_run
  3. workflow_failing_persistently
  4. workflow_no_scheduler
  5. workflow_stale_registration
"""

from .audit import AuditResult, sweep
from .checks import WorkflowStatus, evaluate

__all__ = ["AuditResult", "WorkflowStatus", "evaluate", "sweep"]
