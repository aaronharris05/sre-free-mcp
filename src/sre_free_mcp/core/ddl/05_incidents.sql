-- governance.incidents
--
-- First-class incident lifecycle. An incident groups one or more
-- related gap_reports + events under a single workflow (or scope_id)
-- across the period from first failure to resolution.
--
-- Opened by the incident_management bot when:
--   * a workflow has >= N consecutive failed runs
--   * a single high-severity finding fires
--   * the retry orchestrator marks a workflow exhausted
--
-- Closed when:
--   * all linked gap_reports get resolved_at set
--   * the workflow returns to green in pipeline_health_v1

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.incidents` (
  id STRING NOT NULL,
  workflow_name STRING,            -- nullable; some incidents span scopes
  scope STRING,                    -- mirrors gap_reports.scope when single-scope

  opened_at TIMESTAMP NOT NULL,
  closed_at TIMESTAMP,
  severity STRING NOT NULL,        -- 'low' | 'medium' | 'high' | 'critical'

  summary STRING,
  cause STRING,                    -- filled by RCA bot once available

  -- Cross-references. Stored as arrays for easy lookup.
  linked_finding_ids ARRAY<STRING>,
  linked_event_ids ARRAY<STRING>,
  rca_approval_id STRING,          -- pointer into approval_queue when RCA generated

  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(opened_at)
CLUSTER BY workflow_name, severity;
