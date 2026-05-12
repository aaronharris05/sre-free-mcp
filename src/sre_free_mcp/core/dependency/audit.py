"""Dependency audit — sweep orchestrator."""

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

from .checks import Dependency, evaluate
from .parsers import parse_pyproject, parse_requirements

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    sources: int
    dependencies: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    sources: list[Path] | list[str],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
) -> AuditResult:
    """Run one dependency sweep over ``sources``.

    Each entry in ``sources`` is a path to a ``pyproject.toml`` or
    ``requirements.txt``. The parser is selected by file name.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    deps: list[Dependency] = []
    used_sources = 0
    for raw in sources:
        path = Path(raw)
        if not path.is_file():
            logger.warning("dependency audit: source not found, skipping: %s", path)
            continue
        try:
            if path.name == "pyproject.toml":
                deps.extend(parse_pyproject(path))
            elif path.name.endswith(".txt"):
                deps.extend(parse_requirements(path))
            else:
                logger.warning(
                    "dependency audit: unrecognized source %s; "
                    "expected pyproject.toml or *.txt",
                    path,
                )
                continue
            used_sources += 1
        except Exception:
            logger.exception("dependency parse failed for %s", path)
            continue

    findings: list[Finding] = []
    for dep in deps:
        findings.extend(evaluate(dep))

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.dependency",
        )

    logger.info(
        "dependency_complete sources=%d deps=%d findings=%d",
        used_sources,
        len(deps),
        len(findings),
    )
    return AuditResult(
        sources=used_sources,
        dependencies=len(deps),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


__all__ = ["AuditResult", "sweep"]
