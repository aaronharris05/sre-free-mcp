-- governance.cost_daily
--
-- Daily spend rollup pulled from the customer's BigQuery billing
-- export. Pre-aggregating avoids re-summing the multi-GB billing_export
-- table on every cost-anomaly tick.
--
-- One row per (date, service, workflow_name). workflow_name is NULL
-- when the cost can't be tied to a specific workflow via labels.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.cost_daily` (
  usage_date DATE NOT NULL,
  service STRING NOT NULL,          -- 'BigQuery' | 'Cloud Run' | 'Cloud Storage' | ...
  sku STRING,                       -- finer-grained breakdown
  workflow_name STRING,             -- joined via labels.workflow_name on billing_export

  cost_usd FLOAT64 NOT NULL,
  usage_amount FLOAT64,
  usage_unit STRING,

  -- For the cost-anomaly detector — pre-computed baseline so the
  -- detector can do `cost_usd / baseline_avg` cheaply.
  baseline_avg_28d FLOAT64,         -- rolling 28-day average for this (service, workflow)
  z_score FLOAT64,                  -- standardized vs the 28d window

  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY usage_date
CLUSTER BY service, workflow_name;
