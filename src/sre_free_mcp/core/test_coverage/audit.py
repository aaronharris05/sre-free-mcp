"""Test-coverage audit — sweep orchestrator."""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import evaluate
from .parser import parse_cobertura

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    reports: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    coverage_reports: list[Path] | list[str],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
    overall_threshold: float = 0.80,
    module_threshold: float = 0.60,
    min_lines_for_module_check: int = 20,
) -> AuditResult:
    """Run one coverage sweep over ``coverage_reports``."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    findings: list[Finding] = []
    scanned = 0
    for raw in coverage_reports:
        path = Path(raw)
        if not path.is_file():
            logger.warning("test_coverage audit: report not found, skipping: %s", path)
            continue
        try:
            snapshot = parse_cobertura(path)
            scanned += 1
        except Exception:
            logger.exception("test_coverage parse failed for %s", path)
            continue
        findings.extend(
            evaluate(
                snapshot,
                overall_threshold=overall_threshold,
                module_threshold=module_threshold,
                min_lines_for_module_check=min_lines_for_module_check,
            )
        )

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.test_coverage",
        )

    logger.info("test_coverage_complete reports=%d findings=%d", scanned, len(findings))
    return AuditResult(
        reports=scanned,
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


__all__ = ["AuditResult", "sweep"]
