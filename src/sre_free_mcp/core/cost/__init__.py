"""Cost anomaly audit — flag service/workflow daily spend that has
spiked against its 28-day baseline.

Reads pre-aggregated ``governance.cost_daily`` rows (populated by a
separate ETL that pulls from the customer's BigQuery billing export).
Applies a z-score threshold on top of the rolling 28d baseline already
computed in that table.
"""

from .audit import AuditResult, CostRow, sweep
from .checks import evaluate

__all__ = ["AuditResult", "CostRow", "evaluate", "sweep"]
