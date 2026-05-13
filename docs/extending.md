# Adding a new bot

The audit-bot pattern is the same across all sixteen built-in bots: a pure-logic `checks.py`, an I/O-orchestrating `audit.py`, optional pydantic config schema, and a one-line registration in the runner task table. End-to-end you're looking at ~200 lines of Python + ~100 lines of tests.

This doc walks through adding a new bot from scratch.

## The contract

Every bot must:

1. **Live under `src/sre_free_mcp/core/<bot_name>/`** with at least `__init__.py`, `checks.py`, `audit.py`.
2. **Export a `sweep()` function** from `audit.py` that takes keyword args including `project`, `bq_client`, optional `tables: GovernanceTables`, optional `now: datetime`, and returns a dataclass with at least a `findings: list[Finding]` field.
3. **Write findings via `core.findings.write_findings`** so the shared deterministic-ID + audit-trail behavior applies.
4. **Use a unique `scope` value** on every `Finding` (typically the bot's name).
5. **Register a task in `runner/tasks.py`** that calls `sweep()`.
6. **Be schedulable** by adding an entry to the Terraform `enabled_tasks` map.

## Walkthrough — a "table_size" audit

Goal: a bot that flags BigQuery tables that have grown more than 10× in the last 7 days. Useful for catching runaway storage growth or ingestion bugs.

### Step 1 — pure-logic rule (`core/table_size/checks.py`)

```python
"""Pure-logic table-size rule. No I/O."""
from __future__ import annotations

from dataclasses import dataclass

from sre_free_mcp.core.models import Finding


@dataclass
class TableSnapshot:
    dataset: str
    table: str
    rows_now: int
    rows_7d_ago: int


def evaluate(
    snapshot: TableSnapshot,
    *,
    growth_factor_yellow: float = 5.0,
    growth_factor_red: float = 10.0,
) -> list[Finding]:
    findings: list[Finding] = []
    fqn = f"{snapshot.dataset}.{snapshot.table}"

    if snapshot.rows_7d_ago < 100:
        # New / nearly-empty table — growth math is meaningless.
        return []

    growth = snapshot.rows_now / snapshot.rows_7d_ago
    if growth < growth_factor_yellow:
        return []

    severity = "high" if growth >= growth_factor_red else "medium"
    findings.append(
        Finding(
            scope="table_size",
            scope_id=fqn,
            gap_kind="table_runaway_growth",
            severity=severity,
            details={
                "dataset": snapshot.dataset,
                "table": snapshot.table,
                "rows_now": snapshot.rows_now,
                "rows_7d_ago": snapshot.rows_7d_ago,
                "growth_factor": growth,
                "rule": f"7d growth factor {growth:.1f}x >= {growth_factor_yellow}x",
            },
        )
    )
    return findings


__all__ = ["TableSnapshot", "evaluate"]
```

Pure functions, no GCP imports, easy to test.

### Step 2 — I/O orchestration (`core/table_size/audit.py`)

```python
"""Table-size sweep — pulls row counts from INFORMATION_SCHEMA, applies evaluate."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_free_mcp.core.findings import write_findings as _write_findings_shared
from sre_free_mcp.core.models import Finding
from sre_free_mcp.core.tables import GovernanceTables

from .checks import TableSnapshot, evaluate

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditResult:
    scanned: int
    findings: list[Finding] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    datasets: list[str],
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    write: bool = True,
) -> AuditResult:
    """Scan tables in the given datasets for runaway growth."""
    if not project:
        raise ValueError("project is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    snapshots = list_snapshots(bq_client, project, datasets)
    findings: list[Finding] = []
    for snap in snapshots:
        findings.extend(evaluate(snap))

    if findings and write:
        _write_findings_shared(
            bq_client,
            findings,
            project=project,
            generated_at=now,
            tables=tables,
            source="sre_free_mcp.core.table_size",
        )

    logger.info("table_size_complete scanned=%d findings=%d",
                len(snapshots), len(findings))
    return AuditResult(
        scanned=len(snapshots),
        findings=findings,
        run_id=run_id,
        generated_at=now,
    )


def list_snapshots(
    bq_client: Any, project: str, datasets: list[str]
) -> list[TableSnapshot]:
    """Stubbable. Tests monkeypatch this to inject fake snapshots."""
    snapshots: list[TableSnapshot] = []
    for ds in datasets:
        sql = f"""
        SELECT table_name, total_rows
        FROM `{project}.{ds}.INFORMATION_SCHEMA.TABLE_STORAGE`
        """
        try:
            for r in bq_client.query(sql).result():
                # Mock: pretend 7d ago = current / 1 (no historical data yet)
                snapshots.append(
                    TableSnapshot(
                        dataset=ds,
                        table=r["table_name"],
                        rows_now=int(r["total_rows"]),
                        rows_7d_ago=int(r["total_rows"]),  # TODO: real history
                    )
                )
        except Exception:
            logger.exception("list_snapshots failed for %s.%s", project, ds)
    return snapshots


__all__ = ["AuditResult", "list_snapshots", "sweep"]
```

`list_snapshots` is monkeypatch-friendly — tests can replace it without ever opening a BQ connection.

### Step 3 — public surface (`core/table_size/__init__.py`)

```python
"""Table-size audit — flag BigQuery tables that have grown more than N× in 7 days."""

from .audit import AuditResult, sweep
from .checks import TableSnapshot, evaluate

__all__ = ["AuditResult", "TableSnapshot", "evaluate", "sweep"]
```

### Step 4 — runner task (`runner/tasks.py`)

Add a new task function and register it:

```python
def table_size_audit(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    import os
    from sre_free_mcp.core.table_size import sweep

    raw = os.environ.get("SRE_TABLE_SIZE_DATASETS", "").strip()
    datasets = [s.strip() for s in raw.split(",") if s.strip()] if raw else []
    result = sweep(
        project=config.install.project_id,
        bq_client=bq_client,
        datasets=datasets,
        tables=_tables(config),
        now=now,
    )
    return _to_dict(result)


TASKS: dict[str, TaskFn] = {
    # ... existing entries ...
    "table_size_audit": table_size_audit,
}
```

That's all the registration the MCP server's `run_task()` and the `sre-runner --task=` CLI need to dispatch it.

### Step 5 — Terraform schedule (`infra/terraform/modules/sre/variables.tf`)

```hcl
variable "enabled_tasks" {
  default = {
    # ... existing entries ...
    table_size_audit = "0 4 * * 1"   # weekly Monday 04:00 UTC
  }
}
```

Re-applying Terraform creates one more Cloud Scheduler job.

### Step 6 — tests (`tests/core/table_size/`)

Two test files following the established pattern.

**`tests/core/table_size/__init__.py`**

```python
# empty
```

**`tests/core/table_size/test_checks.py`**

```python
from __future__ import annotations

from sre_free_mcp.core.table_size.checks import TableSnapshot, evaluate


def _snap(rows_now: int, rows_7d: int) -> TableSnapshot:
    return TableSnapshot(
        dataset="ex", table="t", rows_now=rows_now, rows_7d_ago=rows_7d
    )


def test_no_growth_no_finding():
    assert evaluate(_snap(1000, 1000)) == []


def test_5x_growth_fires_medium():
    findings = evaluate(_snap(5000, 1000))
    matched = [f for f in findings if f.gap_kind == "table_runaway_growth"]
    assert len(matched) == 1
    assert matched[0].severity == "medium"


def test_10x_growth_fires_high():
    findings = evaluate(_snap(10000, 1000))
    matched = [f for f in findings if f.gap_kind == "table_runaway_growth"]
    assert len(matched) == 1
    assert matched[0].severity == "high"


def test_tiny_baseline_does_not_fire():
    """Growth math is meaningless against a near-empty table."""
    assert evaluate(_snap(10000, 50)) == []


def test_tunable_thresholds():
    snap = _snap(3000, 1000)  # 3× growth
    assert evaluate(snap) == []                                 # default 5×
    assert evaluate(snap, growth_factor_yellow=2.5) != []        # tight 2.5×
```

**`tests/core/table_size/test_audit.py`**

```python
from __future__ import annotations

from sre_free_mcp.core.table_size import audit
from sre_free_mcp.core.table_size.checks import TableSnapshot


class _FakeBQ:
    def __init__(self):
        self.inserts = []
    def insert_rows_json(self, table, rows):
        self.inserts.append((table, rows))
        return []


def test_sweep_runs_with_no_datasets(monkeypatch):
    monkeypatch.setattr(audit, "list_snapshots", lambda *a, **kw: [])
    result = audit.sweep(project="p", bq_client=_FakeBQ(), datasets=[])
    assert result.scanned == 0


def test_sweep_writes_findings_for_runaway_growth(monkeypatch):
    monkeypatch.setattr(
        audit, "list_snapshots",
        lambda *a, **kw: [TableSnapshot(dataset="d", table="t",
                                       rows_now=10000, rows_7d_ago=500)]
    )
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, datasets=["d"])
    assert len(result.findings) == 1
    assert bq.inserts  # write_findings wrote to gap_reports
```

### Step 7 — run the tests

```bash
pytest tests/core/table_size/ -v
```

### Step 8 — verify dispatch works

```bash
pytest tests/runner/test_tasks.py::test_registry_contains_expected_tasks
```

You may need to add `"table_size_audit"` to the expected set in that test.

## Patterns to follow

### When your bot has its own config

Add a `targets.py` (or `policies.py`) under `core/<bot>/` with a pydantic model, mirror the schema in `config.py`'s `Config` class, and add a `*.example.yaml` to `config/`. See [`core/anomaly/targets.py`](../src/sre_free_mcp/core/anomaly/targets.py) + [`config/anomaly_targets.example.yaml`](../config/anomaly_targets.example.yaml) for the established pattern.

### When your bot calls an external API

Stub the API call in a module-level function that takes only primitive inputs:

```python
def list_open_incidents(project: str) -> list[AlertIncident]:
    try:
        from google.cloud import monitoring_v3  # lazy import
        # ... API call ...
    except Exception:
        logger.exception("list_open_incidents failed (non-fatal)")
        return []
```

The lazy import keeps the SDK off the test suite's import path. Tests stub the function via `monkeypatch.setattr(my_audit, "list_open_incidents", lambda p: [...])`.

### When your bot needs an LLM

Take `llm: LLMProvider` as a sweep arg. The runner task function gets the provider from config:

```python
def my_audit(*, config: Config, bq_client: Any, now: datetime, **_: Any) -> dict[str, Any]:
    from sre_free_mcp.core.my_audit import sweep
    llm = default_provider(config.install.llm, project=config.install.project_id)
    result = sweep(project=..., llm=llm, ...)
    return _to_dict(result)
```

Handle the `NullLLMProvider` case explicitly — the bot should produce useful output even when no LLM is configured (e.g., deterministic narrative fallback).

### When your bot writes to a new BigQuery table

Add a DDL file under `src/sre_free_mcp/core/ddl/` with the next available numeric prefix (`10_my_table.sql`, `11_my_view.sql`, …). The installer applies in lex order, so order matters when views depend on tables.

Use `${project}` and `${dataset}` as template tokens — the installer substitutes them.

### When your bot's tests need a `Finding` factory

`tests/core/<bot>/conftest.py` can define a small factory if useful, but most bots get away with inline `Finding(scope=..., scope_id=..., ...)` calls in the test body.

## Anti-patterns to avoid

- **Don't write directly to BigQuery without going through `core.findings.write_findings`.** That helper enforces deterministic IDs, audit-trail columns, and consistent error handling.
- **Don't put GCP-API calls inside `checks.py`.** Keep the rule logic pure. `checks.py` should be testable without `google-cloud-*` installed.
- **Don't introduce module-level state in your bot.** All state belongs in BigQuery; module imports are stateless.
- **Don't skip the targets schema if your bot is config-driven.** YAML-driven targets validated by pydantic catch typos at config-load time — the alternative is a 04:00 UTC crash.
- **Don't use unparameterized SQL with user-controlled input.** When you must (BQ doesn't parameterize identifiers for `INFORMATION_SCHEMA` paths), validate against `^[A-Za-z_][A-Za-z0-9_]*$` before string concatenation.

## Promotion checklist for a new bot

- [ ] `core/<bot>/__init__.py`, `checks.py`, `audit.py` written
- [ ] `core/<bot>/targets.py` if config-driven (+ schema in `config.py`)
- [ ] `config/<bot>_targets.example.yaml` if config-driven
- [ ] New DDL files in `core/ddl/` if writing to new tables
- [ ] Task registered in `runner/tasks.py::TASKS`
- [ ] Default cron in Terraform `enabled_tasks`
- [ ] Tests: `tests/core/<bot>/test_checks.py` + `test_audit.py`
- [ ] Doc page: `docs/bots/<bot>.md`
- [ ] Bot index updated: `docs/bots/README.md` + `README.md` table
- [ ] Suite passes locally + CI green
