-- governance.gap_reports
--
-- Every finding from every audit bot. Append-only (audits never
-- mutate prior findings); rows are "closed" by setting resolved_at /
-- resolved_by once the underlying issue is fixed.
--
-- scope discriminates which bot emitted the finding:
--   data | cost | freshness | catalog | ai_gov | security | secret_iam
--   | dependency | scm | test_coverage | pii | cloud_monitoring | wiring

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.gap_reports` (
  id STRING NOT NULL,
  generated_at TIMESTAMP NOT NULL,

  -- Routing.
  scope STRING NOT NULL,          -- which bot
  scope_id STRING NOT NULL,       -- what within the scope (table:column, workflow_name, etc.)
  gap_kind STRING NOT NULL,       -- machine-readable kind: 'anomaly_zscore', 'stale_table', 'missing_business_purpose', ...
  severity STRING NOT NULL,       -- 'low' | 'medium' | 'high' | 'critical'

  -- Free-form detail bag. JSON-stringified by the writer.
  details JSON,

  -- Lifecycle.
  resolved_at TIMESTAMP,
  resolved_by STRING,

  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(generated_at)
CLUSTER BY scope, severity;
