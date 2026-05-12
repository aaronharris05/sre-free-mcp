-- governance.pipeline_health_v1
--
-- Per-workflow traffic-light view. The retry orchestrator queries this
-- to find red workflows; the MCP `pipeline_health()` tool returns
-- aggregates from it.
--
-- traffic_light derivation:
--   red    — any high-severity unresolved finding OR > 1 hour past expected next run
--   yellow — any medium-severity unresolved finding OR last run > 1 day old without scheduler trigger yet
--   green  — no unresolved findings + recent successful run (or workflow is event/manual)

CREATE OR REPLACE VIEW `${project}.${dataset}.pipeline_health_v1` AS
WITH active_workflows AS (
  SELECT
    name AS workflow_name,
    cron,
    trigger_kind,
    idempotent,
    owner_team,
    expected_freshness_hours
  FROM `${project}.${dataset}.workflows`
  WHERE status = 'active' OR status IS NULL
),
recent_runs AS (
  SELECT
    JSON_VALUE(payload, '$.workflow_name') AS workflow_name,
    MAX(occurred_at) AS last_run_at,
    MAX_BY(status, occurred_at) AS last_run_status,
    COUNTIF(occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 24 HOUR) AND status = 'error') AS errors_24h
  FROM `${project}.${dataset}.events`
  WHERE event_kind IN ('workflow_run_complete', 'sre_retry_attempt')
    AND occurred_at >= TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 7 DAY)
  GROUP BY workflow_name
),
open_gaps AS (
  SELECT
    scope_id AS workflow_name,
    COUNT(*) AS open_findings,
    MAX(CASE severity
      WHEN 'critical' THEN 4
      WHEN 'high'     THEN 3
      WHEN 'medium'   THEN 2
      WHEN 'low'      THEN 1
      ELSE 0
    END) AS max_severity_rank,
    ARRAY_AGG(gap_kind ORDER BY generated_at DESC LIMIT 1)[OFFSET(0)] AS worst_gap_kind,
    MAX(generated_at) AS worst_finding_at
  FROM `${project}.${dataset}.gap_reports`
  WHERE resolved_at IS NULL
    -- Only per-workflow findings count toward pipeline_health. Other
    -- scopes (data quality on tables, cost-by-service, etc.) have
    -- their own views and don't fold into a workflow's traffic light.
    AND scope = 'workflow'
  GROUP BY scope_id
)
SELECT
  w.workflow_name,
  w.cron,
  w.trigger_kind,
  w.idempotent,
  w.owner_team,
  COALESCE(r.last_run_at, TIMESTAMP('1970-01-01')) AS last_run_at,
  r.last_run_status,
  COALESCE(r.errors_24h, 0) AS errors_24h,
  COALESCE(g.open_findings, 0) AS open_findings,
  CASE g.max_severity_rank
    WHEN 4 THEN 'critical'
    WHEN 3 THEN 'high'
    WHEN 2 THEN 'medium'
    WHEN 1 THEN 'low'
    ELSE NULL
  END AS worst_severity,
  g.worst_gap_kind,
  g.worst_finding_at,
  CASE
    WHEN COALESCE(g.max_severity_rank, 0) >= 3 THEN 'red'
    WHEN COALESCE(r.errors_24h, 0) >= 1 THEN 'red'
    WHEN COALESCE(g.max_severity_rank, 0) >= 2 THEN 'yellow'
    WHEN w.trigger_kind = 'scheduler'
         AND (r.last_run_at IS NULL
              OR r.last_run_at < TIMESTAMP_SUB(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))
      THEN 'yellow'
    ELSE 'green'
  END AS traffic_light,
  CASE
    WHEN COALESCE(g.max_severity_rank, 0) >= 3 OR COALESCE(r.errors_24h, 0) >= 1 THEN 'red'
    ELSE 'ok'
  END AS status
FROM active_workflows w
LEFT JOIN recent_runs r USING (workflow_name)
LEFT JOIN open_gaps g USING (workflow_name);
