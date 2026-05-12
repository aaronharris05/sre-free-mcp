"""Governance-dataset table-name helper.

Every BigQuery table sre-free-mcp reads or writes lives under one
governance dataset (default name: ``governance``). Customers who
already have a dataset by that name can rename via the
``governance_dataset`` field in ``install.yaml``.

This module gives the rest of the code one place to ask for the
dataset-qualified table name, so the override flows through with no
string surgery at call sites.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GovernanceTables:
    """Resolves dataset-qualified table names.

    All properties return ``"{dataset}.{table}"`` — the orchestrator
    prepends the project ID at SQL time.
    """

    dataset: str = "governance"

    @property
    def events(self) -> str:
        return f"{self.dataset}.events"

    @property
    def workflows(self) -> str:
        return f"{self.dataset}.workflows"

    @property
    def approval_queue(self) -> str:
        return f"{self.dataset}.approval_queue"

    @property
    def gap_reports(self) -> str:
        return f"{self.dataset}.gap_reports"

    @property
    def pipeline_health(self) -> str:
        return f"{self.dataset}.pipeline_health_v1"

    @property
    def incidents(self) -> str:
        return f"{self.dataset}.incidents"

    @property
    def pii_findings(self) -> str:
        return f"{self.dataset}.pii_findings"

    @property
    def cost_daily(self) -> str:
        return f"{self.dataset}.cost_daily"

    @property
    def cloud_monitoring_alerts(self) -> str:
        return f"{self.dataset}.cloud_monitoring_alerts"


__all__ = ["GovernanceTables"]
