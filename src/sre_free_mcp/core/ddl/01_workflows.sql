-- governance.workflows
--
-- Registry of every workflow sre-free-mcp monitors. Customers populate
-- this via the MCP `register_workflow` tool or by INSERTing directly.
-- Everything downstream (run monitoring, retry, cost, freshness) keys
-- off rows in this table.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.workflows` (
  -- Primary identity. Unique per install.
  name STRING NOT NULL,

  -- Scheduling. cron in standard 5-field form; trigger_kind tells the
  -- runner whether to expect periodic runs (scheduler) or sporadic
  -- (event / manual).
  cron STRING,
  trigger_kind STRING,  -- 'scheduler' | 'event' | 'manual'

  -- Retry safety. NULL/FALSE means the retry orchestrator will refuse
  -- to auto-retry on transient failure (HITL required).
  idempotent BOOL,

  -- Routing + documentation. owner_team must match a group key in
  -- recipients.yaml. business_purpose is required by the AI Gov audit.
  owner_team STRING,
  business_purpose STRING,

  -- SLA inputs for downstream audits.
  expected_freshness_hours INT64,        -- stale-data audit threshold
  expected_cost_per_run_usd FLOAT64,     -- cost anomaly baseline
  policy_refs ARRAY<STRING>,             -- AI gov: which policies this workflow attests to

  -- Source-of-truth pointer (for SCM + dependency audits).
  source_path STRING,

  -- Lifecycle.
  status STRING,  -- 'active' | 'deprecated' | 'archived' | 'draft'
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP(),

  -- Provenance — which source emitted this row (audit trail).
  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(created_at)
CLUSTER BY name;
