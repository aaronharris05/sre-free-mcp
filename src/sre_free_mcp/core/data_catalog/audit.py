"""Data catalog audit — sweep orchestrator.

Reads every active workflow from ``governance.workflows`` via the
workflows registry helpers, applies :func:`evaluate`, and writes
findings with scope='catalog'.
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
from sre_free_mcp.core.workflows import list_active

from .checks import evaluate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    workflows: int
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
    """Run one data-catalog audit."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    workflows = list_active(bq_client=bq_client, project=project, tables=tables)
    findings: list[Finding] = []
    for wf in workflows:
        findings.extend(evaluate(wf))

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.data_catalog",
        )

    logger.info(
        "data_catalog_complete workflows=%d findings=%d",
        len(workflows),
        len(findings),
    )
    return AuditResult(
        workflows=len(workflows),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


__all__ = ["AuditResult", "sweep"]
