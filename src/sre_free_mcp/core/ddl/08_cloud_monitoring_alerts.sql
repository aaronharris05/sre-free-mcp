-- governance.cloud_monitoring_alerts
--
-- Cache of currently-firing GCP Cloud Monitoring alerts. Refreshed by
-- the cloud_monitoring bot on its tick. Lets other bots (incidents,
-- rollup) join in alert state without hitting the Monitoring API.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.cloud_monitoring_alerts` (
  id STRING NOT NULL,               -- Monitoring incident ID
  policy_name STRING NOT NULL,      -- e.g. 'projects/p/alertPolicies/12345'
  policy_display_name STRING,

  started_at TIMESTAMP NOT NULL,
  ended_at TIMESTAMP,
  state STRING NOT NULL,            -- 'OPEN' | 'CLOSED'

  -- Routing.
  resource_type STRING,             -- 'cloud_run_job' | 'bigquery_dataset' | ...
  resource_labels JSON,
  workflow_name STRING,             -- when resource labels include workflow_name

  -- Metric that triggered.
  metric_type STRING,
  threshold_value FLOAT64,
  observed_value FLOAT64,

  details JSON,
  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(started_at)
CLUSTER BY state, workflow_name;
