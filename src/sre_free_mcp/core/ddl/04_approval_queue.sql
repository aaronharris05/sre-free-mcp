-- governance.approval_queue
--
-- Human-in-the-loop queue for items that exceed an autonomy threshold:
--   - Retry exhausted → RCA narrative pending review
--   - High-severity gap_report → operator must accept or resolve
--   - Policy-flagged action → blocked pending approval
--
-- Items expire after `expires_at` if not acted on (stays in queue
-- but is_expired logic applies in the UI). Resolution writes back
-- to resolution + resolved_at.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.approval_queue` (
  id STRING NOT NULL,
  requested_at TIMESTAMP NOT NULL,
  requested_by_agent STRING NOT NULL,

  -- What's being requested.
  action_kind STRING NOT NULL,    -- 'retry_exhausted' | 'high_severity_gap' | 'policy_block' | ...
  action_payload JSON,            -- free-form context the reviewer needs

  -- Policy + threshold that triggered the queue (for transparency).
  policy_refs ARRAY<STRING>,
  threshold_evaluated JSON,

  -- Lifecycle.
  status STRING NOT NULL,         -- 'pending' | 'approved' | 'rejected' | 'expired'
  resolution STRING,              -- free-form reviewer note
  resolved_at TIMESTAMP,
  resolved_by STRING,
  expires_at TIMESTAMP,

  -- Narrative (set by RCA bot for retry_exhausted items).
  narrative STRING,

  source STRING,
  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(requested_at)
CLUSTER BY status, action_kind;
