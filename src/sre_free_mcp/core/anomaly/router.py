"""Owner-team routing for anomaly findings.

Looks up the email-recipient group for a given finding. A small
dispatch keyed by ``(table, metric_column)`` → owner_team key in
:class:`sre_free_mcp.config.RecipientsConfig`. The email builder groups
findings by routed team and ships one email per team — so Data is
never bcc'd on a Finance signal and vice versa.

Unknown ``(table, metric_column)`` pairs fall through to the
``fallback`` team (default ``governance_owners``) so a mis-registered
target still reaches someone rather than dropping on the floor.
"""

from __future__ import annotations

from .targets import AnomalyTarget, find


def owner_for(
    targets: list[AnomalyTarget],
    table: str,
    metric_column: str,
    *,
    fallback: str = "governance_owners",
) -> str:
    """Return the recipients-group key a finding should route to."""
    target = find(targets, table, metric_column)
    if target is None:
        return fallback
    return target.owner_team


__all__ = ["owner_for"]
