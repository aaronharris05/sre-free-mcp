-- governance.pii_findings
--
-- Separate from gap_reports because Cloud DLP findings have their own
-- retention semantics (often shorter) and a more constrained shape
-- (info_type, likelihood, column path). The PII Sentinel writes here;
-- the rollup bot mirrors high-severity rows into gap_reports for the
-- general audit surface.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.pii_findings` (
  id STRING NOT NULL,
  generated_at TIMESTAMP NOT NULL,

  -- Where the PII was found.
  dataset STRING NOT NULL,
  table_name STRING NOT NULL,
  column_path STRING,              -- 'a.b.c' for nested STRUCT/REPEATED

  -- What was found.
  info_type STRING NOT NULL,       -- DLP info_type: 'EMAIL_ADDRESS', 'PHONE_NUMBER', ...
  pii_class STRING,                -- normalized class: 'pii.direct' | 'pii.derived' | 'sensitive.financial' | ...
  likelihood STRING,               -- DLP likelihood: 'VERY_UNLIKELY' .. 'VERY_LIKELY'
  sample_count INT64,              -- how many cells DLP sampled
  finding_count INT64,             -- how many cells matched

  severity STRING NOT NULL,        -- mapped from likelihood + pii_class
  details JSON,

  -- Lifecycle (e.g. "added Dataplex pii_class tag → resolved").
  resolved_at TIMESTAMP,
  resolved_by STRING,

  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(generated_at)
CLUSTER BY dataset, table_name;
