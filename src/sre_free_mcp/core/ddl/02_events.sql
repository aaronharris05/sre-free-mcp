-- governance.events
--
-- Append-only audit log. Every bot writes one event per action it
-- takes (retry attempt, anomaly scan complete, finding emitted, …).
-- The retry circuit breaker counts rows in this table for its open/
-- closed state. The pipeline_health_v1 view aggregates from here.

CREATE TABLE IF NOT EXISTS `${project}.${dataset}.events` (
  -- Identity + time.
  id STRING NOT NULL,
  occurred_at TIMESTAMP NOT NULL,

  -- Who emitted it. agent_id is the bot name (e.g. 'sre_retry',
  -- 'data_anomaly'); trace_id correlates events across one logical
  -- operation.
  agent_id STRING NOT NULL,
  trace_id STRING,

  -- What happened. event_kind is a stable string ('sre_retry_attempt',
  -- 'anomaly_scan_complete', 'finding_emitted'); payload is free-form
  -- JSON the consumer parses.
  event_kind STRING NOT NULL,
  payload JSON,

  -- Outcome. 'ok' | 'blocked' | 'error'.
  status STRING,

  ingest_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP()
)
PARTITION BY DATE(occurred_at)
CLUSTER BY agent_id, event_kind;
