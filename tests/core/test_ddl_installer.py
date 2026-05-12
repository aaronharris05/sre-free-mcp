"""Unit tests for the DDL installer."""

from __future__ import annotations

from typing import Any

import pytest

from sre_free_mcp.core.ddl_installer import install, list_ddl_files, render_ddl
from sre_free_mcp.core.tables import GovernanceTables


class _FakeBQ:
    def __init__(self) -> None:
        self.queries: list[str] = []

    def query(self, sql: str) -> "_FakeJob":
        self.queries.append(sql)
        return _FakeJob()


class _FakeJob:
    def result(self) -> list[Any]:
        return []


def test_list_ddl_files_returns_sorted_sql_files():
    files = list_ddl_files()
    assert all(f.endswith(".sql") for f in files)
    assert files == sorted(files)
    assert len(files) >= 8  # at least the 8 tables + view


def test_list_ddl_files_includes_each_expected_table():
    files = list_ddl_files()
    joined = "\n".join(files)
    for expected in (
        "workflows",
        "events",
        "gap_reports",
        "approval_queue",
        "incidents",
        "pii_findings",
        "cost_daily",
        "cloud_monitoring_alerts",
        "pipeline_health_v1",
    ):
        assert expected in joined, f"missing DDL file for {expected}"


def test_render_ddl_substitutes_project_and_dataset():
    sql = render_ddl("01_workflows.sql", project="my-proj", dataset="sre")
    assert "${project}" not in sql
    assert "${dataset}" not in sql
    assert "`my-proj.sre.workflows`" in sql


def test_render_ddl_with_default_dataset():
    sql = render_ddl("02_events.sql", project="p", dataset="governance")
    assert "`p.governance.events`" in sql


def test_install_runs_every_file_in_order():
    bq = _FakeBQ()
    applied = install(project="my-proj", bq_client=bq)
    assert applied == list_ddl_files()
    assert len(bq.queries) == len(applied)
    # Every query should have been substituted (no template tokens left).
    for q in bq.queries:
        assert "${project}" not in q
        assert "${dataset}" not in q


def test_install_uses_custom_dataset_name():
    bq = _FakeBQ()
    install(project="my-proj", bq_client=bq, tables=GovernanceTables(dataset="sre"))
    assert all(".sre." in q or "sre_free_mcp" in q for q in bq.queries)


def test_install_requires_project():
    bq = _FakeBQ()
    with pytest.raises(ValueError, match="project is required"):
        install(project="", bq_client=bq)


def test_install_view_comes_after_tables():
    """The pipeline_health_v1 view references workflows/events/gap_reports;
    those tables must be created first."""
    files = list_ddl_files()
    view_idx = next(i for i, f in enumerate(files) if "pipeline_health" in f)
    workflows_idx = next(i for i, f in enumerate(files) if "workflows" in f)
    events_idx = next(i for i, f in enumerate(files) if "events" in f)
    gap_idx = next(i for i, f in enumerate(files) if "gap_reports" in f)
    assert view_idx > workflows_idx
    assert view_idx > events_idx
    assert view_idx > gap_idx


# ---------------------------------------------------------------------------
# Sanity: rendered DDL is structurally plausible
# ---------------------------------------------------------------------------


def test_workflows_ddl_has_required_columns():
    sql = render_ddl("01_workflows.sql", project="p", dataset="g")
    for col in (
        "name",
        "cron",
        "idempotent",
        "owner_team",
        "business_purpose",
        "expected_freshness_hours",
        "expected_cost_per_run_usd",
        "source_path",
        "status",
    ):
        assert col in sql, f"workflows DDL missing column: {col}"


def test_gap_reports_ddl_has_scope_and_severity():
    sql = render_ddl("03_gap_reports.sql", project="p", dataset="g")
    assert "scope STRING NOT NULL" in sql
    assert "severity STRING NOT NULL" in sql
    assert "resolved_at" in sql


def test_view_ddl_uses_create_or_replace():
    """Re-running install should not fail when the view already exists."""
    sql = render_ddl("09_pipeline_health_v1.sql", project="p", dataset="g")
    assert "CREATE OR REPLACE VIEW" in sql
