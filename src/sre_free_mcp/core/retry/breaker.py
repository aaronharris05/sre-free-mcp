"""Circuit breaker for the retry orchestrator.

State is implicit in the ``governance.events`` table: each retry
attempt writes one row with ``event_kind = 'sre_retry_attempt'``. The
breaker counts those rows for a workflow within
``policy.breaker_window_min``.

Three states:

* ``closed`` — count below threshold; new retry allowed.
* ``open``   — count >= threshold; no retry allowed. Stays open until
  the window slides past enough events to drop below threshold.
* ``half_open`` — open, but no NEW failures in the last 30 minutes.
  Exactly one trial allowed; if it succeeds the breaker re-closes on
  the next tick (count naturally drops as the window slides); if it
  fails the breaker stays open with another count toward threshold.

The half-open semantics are operationally lighter than a full state
machine: the orchestrator queries :func:`state_for` each tick and makes
a fresh decision. No persisted state machine to corrupt.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .policy import RetryPolicy

_HALF_OPEN_QUIESCE_MIN = 30

# Default governance dataset name. Customers who rename can thread an
# override through here once the config layer lands.
_EVENTS_TABLE = "governance.events"


def state_for(
    workflow_name: str,
    *,
    bq_client: Any,
    project: str,
    now: datetime,
    policy: RetryPolicy,
    events_table: str = _EVENTS_TABLE,
) -> str:
    """Return ``'closed'`` | ``'open'`` | ``'half_open'``.

    Counts ``sre_retry_attempt`` rows in ``governance.events`` for the
    given workflow within ``policy.breaker_window_min``. Pure-read; no
    state writes. Returns ``'closed'`` on any query error so the breaker
    fails open (allowing retries) — better than failing closed and
    stalling the fleet on a BQ blip.
    """
    window_start = now - timedelta(minutes=policy.breaker_window_min)
    quiesce_start = now - timedelta(minutes=_HALF_OPEN_QUIESCE_MIN)
    sql = f"""
    WITH rows AS (
      SELECT occurred_at
      FROM `{project}.{events_table}`
      WHERE event_kind = 'sre_retry_attempt'
        AND JSON_VALUE(payload, '$.workflow_name') = @workflow_name
        AND occurred_at >= @window_start
    )
    SELECT
      COUNT(*) AS total,
      COUNTIF(occurred_at >= @quiesce_start) AS recent
    FROM rows
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [
            bigquery.ScalarQueryParameter("workflow_name", "STRING", workflow_name),
            bigquery.ScalarQueryParameter("window_start", "TIMESTAMP", window_start),
            bigquery.ScalarQueryParameter("quiesce_start", "TIMESTAMP", quiesce_start),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        # Fail-open: BQ hiccup shouldn't stall the fleet.
        return "closed"

    total = int(rows[0]["total"]) if rows else 0
    recent = int(rows[0]["recent"]) if rows else 0

    if total < policy.breaker_threshold:
        return "closed"
    if recent == 0:
        return "half_open"
    return "open"


__all__ = ["state_for"]
