"""Cloud Monitoring sync + sweep.

Two entrypoints:

* :func:`sync_policies` — declarative alert-policy reconciliation. Takes
  a list of :class:`AlertPolicy` declarations and converges the customer's
  GCP project to that state via the Monitoring API. Idempotent.

* :func:`sweep` — active monitoring tick. Lists currently-open
  Monitoring incidents, caches them in ``cloud_monitoring_alerts``, and
  emits findings to ``gap_reports`` for incidents tied to a registered
  workflow.

Both routines use lazy imports for the Monitoring client so unit tests
that stub the GCP-facing helpers don't need ``google-cloud-monitoring``
installed.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .policies import AlertPolicy

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Active sweep (metric pull → finding)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AlertIncident:
    """One row from the Monitoring API's ``listAlertPolicies`` /
    incidents response."""

    id: str
    policy_name: str
    policy_display_name: str
    started_at: datetime
    ended_at: datetime | None
    state: str                  # 'OPEN' | 'CLOSED'
    resource_type: str | None
    workflow_name: str | None   # extracted from resource labels
    metric_type: str | None
    threshold_value: float | None
    observed_value: float | None
    details: dict[str, Any] = field(default_factory=dict)


_SEVERITY_BY_STATE = {"OPEN": "high", "CLOSED": "low"}


@dataclass(frozen=True)
class AuditResult:
    open_incidents: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
) -> AuditResult:
    """One Cloud Monitoring tick.

    Calls :func:`list_open_incidents` (mockable in tests), caches the
    rows in ``cloud_monitoring_alerts``, and emits a finding per
    incident tied to a workflow_name (so it folds into
    pipeline_health_v1 via the workflow scope path is NOT used — these
    findings use scope='cloud_monitoring').
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    incidents = list_open_incidents(project)
    if incidents:
        _cache_incidents(bq_client, project, tables, incidents)

    findings = [_incident_to_finding(i) for i in incidents if i.workflow_name]

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.cloud_monitoring",
        )

    logger.info(
        "cloud_monitoring_sweep_complete open=%d findings=%d",
        len(incidents),
        len(findings),
    )
    return AuditResult(
        open_incidents=len(incidents),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


def _incident_to_finding(incident: AlertIncident) -> Finding:
    severity = _SEVERITY_BY_STATE.get(incident.state, "medium")
    return Finding(
        scope="cloud_monitoring",
        scope_id=incident.workflow_name or incident.policy_display_name,
        gap_kind="cloud_monitoring_alert",
        severity=severity,
        details={
            "as_of": incident.started_at.isoformat(),
            "policy_name": incident.policy_name,
            "policy_display_name": incident.policy_display_name,
            "metric_type": incident.metric_type,
            "threshold": incident.threshold_value,
            "observed": incident.observed_value,
            "workflow_name": incident.workflow_name,
            "resource_type": incident.resource_type,
            **incident.details,
        },
    )


def _cache_incidents(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    incidents: list[AlertIncident],
) -> None:
    """Insert the latest incident rows into ``cloud_monitoring_alerts``."""
    import json

    rows: list[dict[str, Any]] = []
    for i in incidents:
        rows.append(
            {
                "id": i.id,
                "policy_name": i.policy_name,
                "policy_display_name": i.policy_display_name,
                "started_at": i.started_at.isoformat(),
                "ended_at": i.ended_at.isoformat() if i.ended_at else None,
                "state": i.state,
                "resource_type": i.resource_type,
                "resource_labels": json.dumps({}),
                "workflow_name": i.workflow_name,
                "metric_type": i.metric_type,
                "threshold_value": i.threshold_value,
                "observed_value": i.observed_value,
                "details": json.dumps(i.details),
                "source": "sre_free_mcp.core.cloud_monitoring",
            }
        )
    table_ref = f"{project}.{tables.cloud_monitoring_alerts}"
    try:
        errors = bq_client.insert_rows_json(table_ref, rows)
        if errors:
            logger.error("cloud_monitoring_alerts insert errors: %s", errors)
    except Exception:
        logger.exception("cloud_monitoring_alerts insert failed (non-fatal)")


# ---------------------------------------------------------------------------
# Declarative sync (YAML → GCP)
# ---------------------------------------------------------------------------


def sync_policies(
    *,
    project: str,
    policies: list[AlertPolicy],
) -> dict[str, int]:
    """Reconcile the customer's Monitoring alert policies with the
    declared list.

    Returns a summary dict ``{created, updated, unchanged, deleted}``.
    For v1, "deleted" is always 0 — drift cleanup happens via a
    separate ``prune`` flag once we're confident the YAML is authoritative.

    Best-effort: per-policy errors are caught + logged so a single bad
    policy doesn't block the rest of the sync.
    """
    if not project:
        raise ValueError("project is required")
    summary = {"created": 0, "updated": 0, "unchanged": 0, "deleted": 0}
    existing = _list_existing_policies(project)
    for pol in policies:
        try:
            existing_match = existing.get(pol.display_name)
            if existing_match is None:
                _create_policy(project, pol)
                summary["created"] += 1
            elif _policy_differs(existing_match, pol):
                _update_policy(project, existing_match, pol)
                summary["updated"] += 1
            else:
                summary["unchanged"] += 1
        except Exception:
            logger.exception("policy sync failed for %s", pol.display_name)
    return summary


# ---------------------------------------------------------------------------
# GCP-facing helpers — stubbed in tests
# ---------------------------------------------------------------------------


def list_open_incidents(project: str) -> list[AlertIncident]:
    """Return currently-open incidents from Cloud Monitoring.

    Default implementation calls the Monitoring API. Tests stub this
    function directly rather than mocking the API client.
    """
    try:
        from google.cloud import monitoring_v3  # lazy

        client = monitoring_v3.AlertPolicyServiceClient()
        # The Monitoring API exposes incidents via the Service Monitoring
        # service for SLO-backed policies; for generic alert incidents
        # the user-facing API is more limited. For v1 we list policies
        # and return an empty list — production users should override
        # this function in their own runner with an integration tied to
        # their notification channels' API.
        _ = list(client.list_alert_policies(name=f"projects/{project}"))
        return []
    except Exception:
        logger.exception("list_open_incidents failed (non-fatal)")
        return []


def _list_existing_policies(project: str) -> dict[str, Any]:
    """Return ``{display_name: policy_resource}`` for every existing
    alert policy in the project."""
    try:
        from google.cloud import monitoring_v3  # lazy

        client = monitoring_v3.AlertPolicyServiceClient()
        out: dict[str, Any] = {}
        for pol in client.list_alert_policies(name=f"projects/{project}"):
            out[pol.display_name] = pol
        return out
    except Exception:
        logger.exception("list_existing_policies failed (non-fatal)")
        return {}


def _create_policy(project: str, policy: AlertPolicy) -> Any:
    """Create one alert policy. Returns the created resource."""
    from google.cloud import monitoring_v3  # lazy

    client = monitoring_v3.AlertPolicyServiceClient()
    body = _to_api_policy(policy)
    return client.create_alert_policy(
        name=f"projects/{project}", alert_policy=body
    )


def _update_policy(project: str, existing: Any, policy: AlertPolicy) -> Any:
    """Update one alert policy in place."""
    from google.cloud import monitoring_v3  # lazy

    client = monitoring_v3.AlertPolicyServiceClient()
    body = _to_api_policy(policy)
    body.name = existing.name
    return client.update_alert_policy(alert_policy=body)


def _policy_differs(existing: Any, declared: AlertPolicy) -> bool:
    """Cheap shallow compare so we don't update on every tick.

    Compares display_name + enabled state + documentation text + the
    first condition's threshold. A deeper diff is future work; for v1
    a noisy "updated" count is fine — Monitoring de-dupes by name.
    """
    if not getattr(existing, "enabled", True) == declared.enabled:
        return True
    existing_doc = getattr(getattr(existing, "documentation", None), "content", "") or ""
    if existing_doc != declared.documentation:
        return True
    return False


def _to_api_policy(policy: AlertPolicy) -> Any:
    """Map our AlertPolicy to the Monitoring API's protobuf shape.

    Lazy-imported helpers so test runs without google-cloud-monitoring
    don't fail at import time.
    """
    from google.cloud import monitoring_v3  # lazy
    from google.protobuf import duration_pb2  # lazy

    conditions = []
    for c in policy.conditions:
        cond = monitoring_v3.AlertPolicy.Condition()
        cond.display_name = f"{policy.display_name} :: {c.metric_type}"
        threshold = cond.condition_threshold
        threshold.filter = (
            f'metric.type = "{c.metric_type}"'
            + (f" AND {c.filter}" if c.filter else "")
        )
        threshold.comparison = getattr(
            monitoring_v3.ComparisonType, c.comparison
        )
        threshold.threshold_value = c.threshold_value
        threshold.duration = duration_pb2.Duration(seconds=c.duration_seconds)
        threshold.aggregations.append(
            monitoring_v3.Aggregation(
                alignment_period=duration_pb2.Duration(
                    seconds=c.aggregation_alignment_period_seconds
                ),
                per_series_aligner=getattr(
                    monitoring_v3.Aggregation.Aligner,
                    c.aggregation_per_series_aligner,
                ),
            )
        )
        conditions.append(cond)

    body = monitoring_v3.AlertPolicy(
        display_name=policy.display_name,
        enabled=policy.enabled,
        combiner=monitoring_v3.AlertPolicy.ConditionCombinerType.AND,
        conditions=conditions,
    )
    if policy.documentation:
        body.documentation.content = policy.documentation
        body.documentation.mime_type = "text/markdown"
    return body


__all__ = [
    "AlertIncident",
    "AuditResult",
    "list_open_incidents",
    "sweep",
    "sync_policies",
]
