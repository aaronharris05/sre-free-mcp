"""Apply the governance DDL to a customer's BigQuery project.

Reads ``.sql`` files from :mod:`sre_free_mcp.core.ddl`, substitutes
``${project}`` and ``${dataset}`` from :class:`GovernanceTables` +
``install.yaml``, and runs each statement against BigQuery.

Idempotent — every statement is ``CREATE TABLE IF NOT EXISTS`` or
``CREATE OR REPLACE VIEW``, so re-running is safe and equivalent to a
no-op once the schema exists.
"""

from __future__ import annotations

import logging
from importlib import resources
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables

logger = logging.getLogger(__name__)


def list_ddl_files() -> list[str]:
    """Return sorted DDL filenames bundled with the package.

    Files are numbered ``01_`` / ``02_`` / … and applied in order so
    table creation precedes the view that depends on them.
    """
    ddl_pkg = resources.files("sre_free_mcp.core.ddl")
    return sorted(
        entry.name
        for entry in ddl_pkg.iterdir()
        if entry.is_file() and entry.name.endswith(".sql")
    )


def render_ddl(filename: str, *, project: str, dataset: str) -> str:
    """Read one DDL file and substitute ``${project}`` / ``${dataset}``."""
    ddl_pkg = resources.files("sre_free_mcp.core.ddl")
    raw = (ddl_pkg / filename).read_text(encoding="utf-8")
    return raw.replace("${project}", project).replace("${dataset}", dataset)


def install(
    *,
    project: str,
    bq_client: Any,
    tables: GovernanceTables | None = None,
) -> list[str]:
    """Apply every DDL file in order. Returns the list of filenames applied.

    Args:
        project: GCP project to create the schema in.
        bq_client: a ``google.cloud.bigquery.Client``.
        tables: dataset-name override; defaults to ``governance``.
    """
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    applied: list[str] = []
    for filename in list_ddl_files():
        sql = render_ddl(filename, project=project, dataset=tables.dataset)
        logger.info("ddl_apply file=%s dataset=%s", filename, tables.dataset)
        bq_client.query(sql).result()
        applied.append(filename)
    return applied


__all__ = ["install", "list_ddl_files", "render_ddl"]
