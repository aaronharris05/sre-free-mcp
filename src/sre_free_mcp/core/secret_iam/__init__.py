"""Secret Manager + project IAM audit.

Two checks operating against one GCP project:

  1. Secret rotation age. Each secret's most-recent enabled version's
     create_time is compared against ``rotation_threshold_days``.
  2. Project IAM hygiene. Bindings granting ``roles/owner`` or
     ``roles/editor`` to user / service-account principals are flagged.
     Public bindings (``allUsers`` / ``allAuthenticatedUsers``) on any
     role are flagged critical.

Per-secret IAM bindings, audit-log driven usage detection, and CAIEP
analyzer integration are deferred to v2.
"""

from .audit import AuditResult, sweep
from .checks import SecretSnapshot, evaluate_iam_policy, evaluate_secret

__all__ = [
    "AuditResult",
    "SecretSnapshot",
    "evaluate_iam_policy",
    "evaluate_secret",
    "sweep",
]
