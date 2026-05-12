"""Cloud Monitoring integration.

Two responsibilities:

1. **Alert sync (declarative).** Reads alert policy definitions from
   ``alert_policies.yaml`` and applies them to the customer's GCP
   project via the Monitoring API. Idempotent — re-running converges
   the project to the desired state.

2. **Metric pull (active).** Fetches a list of currently-firing alert
   incidents from the Monitoring API, caches them in
   ``governance.cloud_monitoring_alerts``, and emits gap_reports
   findings for incidents tied to a registered workflow.
"""

from .audit import AuditResult, sweep, sync_policies
from .policies import AlertPoliciesConfig, AlertPolicy

__all__ = [
    "AlertPoliciesConfig",
    "AlertPolicy",
    "AuditResult",
    "sweep",
    "sync_policies",
]
