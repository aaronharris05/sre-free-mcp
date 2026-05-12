"""RCA sweep — Gemini narrative for pending approval_queue items."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.llm import NULL_LLM_SENTINEL, LLMProvider, NullLLMProvider

logger = logging.getLogger(__name__)

_DEFAULT_ACTION_KINDS: tuple[str, ...] = ("retry_exhausted",)


@dataclass(frozen=True)
class AuditResult:
    pending: int                  # how many candidates were eligible
    generated: int                # how many narratives we actually wrote
    skipped_existing: int         # had a narrative already; skipped
    skipped_null_llm: int         # null LLM produced no narrative
    item_ids: list[str] = field(default_factory=list)
    run_id: str = ""
    generated_at: datetime | None = None


def sweep(
    *,
    project: str,
    bq_client: Any,
    llm: LLMProvider,
    tables: GovernanceTables | None = None,
    now: datetime | None = None,
    action_kinds: tuple[str, ...] = _DEFAULT_ACTION_KINDS,
    context_window_hours: int = 24,
) -> AuditResult:
    """One RCA tick.

    Reads pending approval_queue items of the given ``action_kinds``,
    builds a context bundle (recent events + open findings for the
    affected workflow), asks ``llm.generate`` for a 3-5 sentence
    narrative, and writes it back to the queue row.

    Items that already have a non-empty ``narrative`` are skipped (the
    operator may have edited it; we don't clobber).
    """
    if not project:
        raise ValueError("project is required")
    if llm is None:
        raise ValueError("llm provider is required")
    tables = tables or GovernanceTables()
    now = now or datetime.now(UTC)
    run_id = str(uuid.uuid4())

    pending = _read_pending(bq_client, project, tables, action_kinds=action_kinds)

    generated = 0
    skipped_existing = 0
    skipped_null = 0
    touched: list[str] = []

    for item in pending:
        if item.get("narrative"):
            skipped_existing += 1
            continue
        workflow_name = _extract_workflow_name(item.get("action_payload"))
        events = _recent_events(
            bq_client,
            project,
            tables,
            workflow_name=workflow_name,
            now=now,
            window_hours=context_window_hours,
        )
        findings = _open_findings(
            bq_client, project, tables, workflow_name=workflow_name
        )
        prompt = _build_prompt(item, events=events, findings=findings, now=now)

        if isinstance(llm, NullLLMProvider):
            skipped_null += 1
            continue

        try:
            narrative = llm.generate(prompt, max_tokens=400, temperature=0.2)
        except Exception:
            logger.exception(
                "rca llm.generate failed for queue item %s (non-fatal)", item["id"]
            )
            continue

        if NULL_LLM_SENTINEL in narrative or not narrative.strip():
            skipped_null += 1
            continue

        _write_narrative(bq_client, project, tables, item["id"], narrative.strip(), now)
        touched.append(item["id"])
        generated += 1

    logger.info(
        "rca_complete pending=%d generated=%d skipped_existing=%d skipped_null=%d",
        len(pending),
        generated,
        skipped_existing,
        skipped_null,
    )
    return AuditResult(
        pending=len(pending),
        generated=generated,
        skipped_existing=skipped_existing,
        skipped_null_llm=skipped_null,
        item_ids=touched,
        run_id=run_id,
        generated_at=now,
    )


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _read_pending(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    action_kinds: tuple[str, ...],
) -> list[dict[str, Any]]:
    in_list = ", ".join(f"'{k}'" for k in action_kinds)
    sql = f"""
    SELECT id, action_kind, action_payload, narrative, requested_at
    FROM `{project}.{tables.approval_queue}`
    WHERE status = 'pending'
      AND action_kind IN ({in_list})
    ORDER BY requested_at ASC
    """
    try:
        rows = list(bq_client.query(sql).result())
    except Exception:
        logger.exception("rca read_pending failed (non-fatal)")
        return []
    return [
        {
            "id": r["id"],
            "action_kind": r["action_kind"],
            "action_payload": r["action_payload"],
            "narrative": r["narrative"],
            "requested_at": r["requested_at"],
        }
        for r in rows
    ]


def _recent_events(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    workflow_name: str | None,
    now: datetime,
    window_hours: int,
) -> list[dict[str, Any]]:
    if not workflow_name:
        return []
    sql = f"""
    SELECT occurred_at, event_kind, payload, status
    FROM `{project}.{tables.events}`
    WHERE JSON_VALUE(payload, '$.workflow_name') = @name
      AND occurred_at >= @window_start
    ORDER BY occurred_at DESC
    LIMIT 50
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [
            bigquery.ScalarQueryParameter("name", "STRING", workflow_name),
            bigquery.ScalarQueryParameter(
                "window_start", "TIMESTAMP", now - timedelta(hours=window_hours)
            ),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("rca recent_events read failed (non-fatal)")
        return []
    return [
        {
            "occurred_at": r["occurred_at"],
            "event_kind": r["event_kind"],
            "payload": r["payload"],
            "status": r["status"],
        }
        for r in rows
    ]


def _open_findings(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    *,
    workflow_name: str | None,
) -> list[dict[str, Any]]:
    if not workflow_name:
        return []
    sql = f"""
    SELECT generated_at, scope, gap_kind, severity, details
    FROM `{project}.{tables.gap_reports}`
    WHERE scope_id = @name AND resolved_at IS NULL
    ORDER BY generated_at DESC
    LIMIT 20
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [bigquery.ScalarQueryParameter("name", "STRING", workflow_name)]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(bq_client.query(sql, job_config=job_config).result())
    except Exception:
        logger.exception("rca open_findings read failed (non-fatal)")
        return []
    return [
        {
            "generated_at": r["generated_at"],
            "scope": r["scope"],
            "gap_kind": r["gap_kind"],
            "severity": r["severity"],
            "details": r["details"],
        }
        for r in rows
    ]


# ---------------------------------------------------------------------------
# Prompt + write
# ---------------------------------------------------------------------------


_RCA_PROMPT = """
You are an on-call SRE assistant writing a brief root-cause narrative
for a workflow that has exhausted its automated retries. Write 3-5
plain-text sentences that:

  - Name the workflow + when the trouble started (use action_payload).
  - Summarize the recent events / findings as evidence (do not enumerate;
    treat them as flavor).
  - Offer the single most likely root cause based on the gap_kinds + status
    values in the evidence. Be specific: "Cloud Run quota exhaustion"
    rather than "infrastructure issue".
  - Recommend the next operator action — usually one of: bump a quota,
    fix a config drift, escalate to the workflow owner, or accept the
    failure for now.

No markdown, no preamble like "Root cause analysis:", no emoji. If the
evidence is ambiguous say so plainly.

Action context:
{action}

Recent events ({event_count}):
{events}

Open findings ({finding_count}):
{findings}
""".strip()


def _build_prompt(
    item: dict[str, Any],
    *,
    events: list[dict[str, Any]],
    findings: list[dict[str, Any]],
    now: datetime,
) -> str:
    action = {
        "id": item["id"],
        "action_kind": item["action_kind"],
        "requested_at": _isoformat(item.get("requested_at")),
        "payload": _parse_json(item.get("action_payload")),
    }
    return _RCA_PROMPT.format(
        action=json.dumps(action, indent=2, default=str),
        event_count=len(events),
        events=json.dumps(
            [
                {
                    "occurred_at": _isoformat(e["occurred_at"]),
                    "event_kind": e["event_kind"],
                    "status": e["status"],
                    "payload": _parse_json(e["payload"]),
                }
                for e in events[:20]
            ],
            indent=2,
            default=str,
        ),
        finding_count=len(findings),
        findings=json.dumps(
            [
                {
                    "generated_at": _isoformat(f["generated_at"]),
                    "scope": f["scope"],
                    "gap_kind": f["gap_kind"],
                    "severity": f["severity"],
                    "details": _parse_json(f["details"]),
                }
                for f in findings[:10]
            ],
            indent=2,
            default=str,
        ),
    )


def _write_narrative(
    bq_client: Any,
    project: str,
    tables: GovernanceTables,
    item_id: str,
    narrative: str,
    now: datetime,
) -> None:
    sql = f"""
    UPDATE `{project}.{tables.approval_queue}`
    SET narrative = @narrative
    WHERE id = @id
    """
    try:
        from google.cloud import bigquery  # lazy

        params = [
            bigquery.ScalarQueryParameter("id", "STRING", item_id),
            bigquery.ScalarQueryParameter("narrative", "STRING", narrative),
        ]
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        bq_client.query(sql, job_config=job_config).result()
    except Exception:
        logger.exception("rca write_narrative failed (non-fatal)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_workflow_name(payload: Any) -> str | None:
    parsed = _parse_json(payload)
    if isinstance(parsed, dict):
        wf = parsed.get("workflow_name")
        if isinstance(wf, str) and wf:
            return wf
    return None


def _parse_json(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _isoformat(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


__all__ = ["AuditResult", "sweep"]
