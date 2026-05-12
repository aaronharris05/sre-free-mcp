"""AI Governance audit — verify every workflow attests to policy compliance.

Beyond the structural completeness audited by data_catalog, the AI
governance audit checks that each workflow has the metadata an
organisation typically requires for AI / data-handling artifacts:

  - ``missing_business_purpose`` — no business_purpose field. Policy 01
    §2.4 (and equivalent in most orgs) requires every AI/data artifact
    document its business purpose.
  - ``business_purpose_too_terse`` — set but below a configurable
    character minimum (default 30). Catches "TODO" / "test" / empty
    boilerplate.
  - ``missing_policy_refs`` — when ``require_policy_refs=True``
    (default False). Useful for regulated installs.
"""

from .audit import AuditResult, sweep
from .checks import evaluate

__all__ = ["AuditResult", "evaluate", "sweep"]
