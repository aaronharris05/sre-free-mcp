"""Secret Manager + IAM audit — sweep orchestrator."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import (
    IamBinding,
    SecretSnapshot,
    evaluate_iam_policy,
    evaluate_secret,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    secrets_scanned: int
    iam_bindings_scanned: int
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
    rotation_threshold_days: int = 90,
) -> AuditResult:
    """One Secret Manager + project IAM audit."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    secrets = list_secrets(project)
    bindings = read_project_iam_bindings(project)

    findings: list[Finding] = []
    for s in secrets:
        findings.extend(
            evaluate_secret(s, now, rotation_threshold_days=rotation_threshold_days)
        )
    findings.extend(evaluate_iam_policy(bindings, project_id=project))

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.secret_iam",
        )

    logger.info(
        "secret_iam_complete secrets=%d bindings=%d findings=%d",
        len(secrets),
        len(bindings),
        len(findings),
    )
    return AuditResult(
        secrets_scanned=len(secrets),
        iam_bindings_scanned=len(bindings),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# GCP-facing helpers — stubbed in tests
# ---------------------------------------------------------------------------


def list_secrets(project: str) -> list[SecretSnapshot]:
    """List every secret in ``project`` with its latest enabled-version
    timestamp."""
    try:
        from google.cloud import secretmanager  # lazy

        client = secretmanager.SecretManagerServiceClient()
        parent = f"projects/{project}"
        out: list[SecretSnapshot] = []
        for secret in client.list_secrets(parent=parent):
            short = secret.name.split("/")[-1]
            enabled_versions: list[Any] = [
                v
                for v in client.list_secret_versions(parent=secret.name)
                if getattr(v.state, "name", None) == "ENABLED"
                or getattr(v, "state", None) == secretmanager.SecretVersion.State.ENABLED
            ]
            latest_at = None
            if enabled_versions:
                latest_at = max(
                    (v.create_time for v in enabled_versions),
                    default=None,
                )
            out.append(
                SecretSnapshot(
                    name=short,
                    latest_enabled_version_created_at=latest_at,
                    enabled_version_count=len(enabled_versions),
                )
            )
        return out
    except Exception:
        logger.exception("list_secrets failed (non-fatal)")
        return []


def read_project_iam_bindings(project: str) -> list[IamBinding]:
    """Read the project's IAM policy and return ``[IamBinding, …]``."""
    try:
        from google.cloud import resourcemanager_v3  # lazy

        client = resourcemanager_v3.ProjectsClient()
        policy = client.get_iam_policy(resource=f"projects/{project}")
        out: list[IamBinding] = []
        for b in policy.bindings:
            out.append(IamBinding(role=b.role, members=tuple(b.members)))
        return out
    except Exception:
        logger.exception("read_project_iam_bindings failed (non-fatal)")
        return []


__all__ = [
    "AuditResult",
    "list_secrets",
    "read_project_iam_bindings",
    "sweep",
]
