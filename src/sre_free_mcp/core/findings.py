"""Shared writer for ``governance.gap_reports`` rows.

Every audit bot (anomaly, job_uptime, cost, freshness, …) writes
findings via :func:`write_findings`. Centralizing the writer means the
INSERT shape, deterministic ID generation, and BQ error handling stay
consistent across bots — and a schema migration only has to update one
place.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any

from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)


def write_findings(
    bq_client: Any,
    findings: list[Finding],
    *,
    project: str,
    generated_at: datetime,
    tables: GovernanceTables | None = None,
    source: str | None = None,
) -> None:
    """Bulk-insert findings into ``gap_reports``.

    IDs are deterministic — same ``(scope, scope_id, gap_kind, as_of,
    generated_at)`` tuple always produces the same UUID5. Re-running a
    sweep is therefore idempotent at the row level: BQ insert_rows_json
    accepts duplicates silently when streaming, but downstream queries
    can dedupe on ``id``.

    A failure here raises — audit bots that want to continue on write
    errors should wrap the call.
    """
    if not findings:
        return
    tables = tables or GovernanceTables()
    iso_now = generated_at.isoformat()
    table_ref = f"{project}.{tables.gap_reports}"

    rows: list[dict[str, Any]] = []
    for f in findings:
        as_of = (f.details or {}).get("as_of")
        gid = uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"{f.scope}|{f.scope_id}|{f.gap_kind}|{as_of}|{iso_now}",
        )
        rows.append(
            {
                "id": str(gid),
                "generated_at": iso_now,
                "scope": f.scope,
                "scope_id": f.scope_id,
                "gap_kind": f.gap_kind,
                "severity": f.severity,
                "details": json.dumps(f.details),
                "resolved_at": None,
                "resolved_by": None,
                "source": source,
            }
        )
    errors = bq_client.insert_rows_json(table_ref, rows)
    if errors:
        logger.error("write_findings insert errors: %s", errors)
        raise RuntimeError(f"gap_reports insert failed: {errors}")


__all__ = ["write_findings"]
