"""Security audit — forward Security Command Center findings.

GCP's Security Command Center (SCC) is the canonical aggregator for
GCP security findings. This bot pulls SCC findings via the API and
mirrors them into ``governance.gap_reports`` with scope='security' so
the rest of the SRE stack (rollup, incidents) can act on them.

SCC requires organization-level setup and is a paid product at the
Premium tier. When the SCC client is unavailable or the configured
``organization_id`` is empty, the sweep no-ops cleanly — the rest of
the audit fleet continues working.
"""

from .audit import AuditResult, SccFinding, sweep

__all__ = ["AuditResult", "SccFinding", "sweep"]
