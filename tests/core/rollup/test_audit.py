"""Smoke tests for the rollup sweep."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from sre_free_mcp.core.rollup import audit
from sre_free_mcp.core.rollup.audit import RollupSummary
from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.email_sender import EmailMessage, EmailSender, NullEmailSender

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


def _patch_summary_inputs(
    monkeypatch,
    *,
    findings: list[dict[str, Any]] | None = None,
    open_incidents: int = 0,
    pending: int = 0,
):
    monkeypatch.setattr(audit, "_read_open_findings", lambda *a, **kw: list(findings or []))
    monkeypatch.setattr(audit, "_count_open_incidents", lambda *a, **kw: open_incidents)
    monkeypatch.setattr(audit, "_count_pending_approvals", lambda *a, **kw: pending)


def _patch_promote(monkeypatch, promoted: int = 0):
    monkeypatch.setattr(audit, "_promote_high_severity", lambda *a, **kw: promoted)


def _f(scope: str, sev: str, kind: str, scope_id: str = "wf_a") -> dict[str, Any]:
    return {
        "id": f"id-{kind}-{sev}",
        "generated_at": _NOW - timedelta(hours=1),
        "scope": scope,
        "scope_id": scope_id,
        "gap_kind": kind,
        "severity": sev,
        "details": None,
    }


# ---------------------------------------------------------------------------
# Empty / quiet state
# ---------------------------------------------------------------------------


def test_sweep_no_findings_no_incidents_no_email_sent(monkeypatch):
    _patch_summary_inputs(monkeypatch)
    _patch_promote(monkeypatch)
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    assert result.summary.open_findings_total == 0
    assert result.email_sent is False


def test_sweep_with_no_recipients_does_not_send(monkeypatch):
    _patch_summary_inputs(monkeypatch, findings=[_f("freshness", "high", "table_stale")])
    _patch_promote(monkeypatch)
    sender = NullEmailSender()
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        email_sender=sender,
        recipients=None,
        now=_NOW,
    )
    assert sender.sent == []
    assert result.email_sent is False


# ---------------------------------------------------------------------------
# Summary aggregation
# ---------------------------------------------------------------------------


def test_summary_counts_by_severity_and_scope(monkeypatch):
    findings = [
        _f("freshness", "high", "table_stale", scope_id="a"),
        _f("freshness", "medium", "table_empty", scope_id="b"),
        _f("cost", "critical", "cost_anomaly", scope_id="Cloud Run:wf_a"),
        _f("workflow", "high", "workflow_missed_run", scope_id="wf_a"),
    ]
    _patch_summary_inputs(monkeypatch, findings=findings, open_incidents=2, pending=1)
    _patch_promote(monkeypatch)
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    s = result.summary
    assert s.open_findings_total == 4
    assert s.open_findings_by_severity["high"] == 2
    assert s.open_findings_by_severity["medium"] == 1
    assert s.open_findings_by_severity["critical"] == 1
    assert s.open_findings_by_scope["freshness"] == 2
    assert s.open_findings_by_scope["workflow"] == 1
    assert s.open_incidents == 2
    assert s.pending_approvals == 1


def test_top_findings_sorted_by_severity_then_recency(monkeypatch):
    findings = [
        _f("workflow", "medium", "x", scope_id="wf_med"),
        _f("workflow", "critical", "x", scope_id="wf_crit"),
        _f("workflow", "high", "x", scope_id="wf_high"),
    ]
    _patch_summary_inputs(monkeypatch, findings=findings)
    _patch_promote(monkeypatch)
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    severities = [f["severity"] for f in result.summary.top_findings]
    assert severities[0] == "critical"
    assert severities[1] == "high"
    assert severities[2] == "medium"


def test_top_findings_respects_top_n(monkeypatch):
    findings = [_f("workflow", "high", "x", scope_id=f"wf_{i}") for i in range(10)]
    _patch_summary_inputs(monkeypatch, findings=findings)
    _patch_promote(monkeypatch)
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW, top_n=3)
    assert len(result.summary.top_findings) == 3


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------


class _CapturingSender(EmailSender):
    def __init__(self) -> None:
        self.sent: list[EmailMessage] = []

    def send(self, message: EmailMessage) -> None:
        self.sent.append(message)


def test_sweep_sends_email_when_sender_and_recipients_given(monkeypatch):
    _patch_summary_inputs(
        monkeypatch, findings=[_f("freshness", "high", "table_stale")]
    )
    _patch_promote(monkeypatch)
    sender = _CapturingSender()
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        email_sender=sender,
        recipients=["ops@example.com"],
        now=_NOW,
    )
    assert result.email_sent is True
    assert len(sender.sent) == 1
    msg = sender.sent[0]
    assert msg.to == ["ops@example.com"]
    assert "1 open findings" in msg.subject


def test_sweep_email_does_not_block_on_sender_error(monkeypatch):
    class _RaisingSender(EmailSender):
        def send(self, message: EmailMessage) -> None:
            raise RuntimeError("simulated SMTP outage")

    _patch_summary_inputs(monkeypatch, findings=[_f("workflow", "high", "x")])
    _patch_promote(monkeypatch)
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        email_sender=_RaisingSender(),
        recipients=["ops@example.com"],
        now=_NOW,
    )
    assert result.email_sent is False  # send failed, but no exception


# ---------------------------------------------------------------------------
# Promotion to approval_queue
# ---------------------------------------------------------------------------


def test_sweep_can_disable_promotion(monkeypatch):
    _patch_summary_inputs(
        monkeypatch, findings=[_f("workflow", "high", "x")]
    )

    called: list[Any] = []
    monkeypatch.setattr(
        audit, "_promote_high_severity", lambda *a, **kw: called.append(True) or 0
    )

    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), now=_NOW, promote_high_severity=False
    )
    assert called == []
    assert result.promoted == 0


def test_sweep_threads_promoted_count_into_result(monkeypatch):
    _patch_summary_inputs(monkeypatch)
    _patch_promote(monkeypatch, promoted=4)
    result = audit.sweep(project="p", bq_client=_FakeBQ(), now=_NOW)
    assert result.promoted == 4


# ---------------------------------------------------------------------------
# Required args
# ---------------------------------------------------------------------------


def test_sweep_requires_project(monkeypatch):
    _patch_summary_inputs(monkeypatch)
    _patch_promote(monkeypatch)
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), now=_NOW)


def test_summary_dataclass_is_frozen():
    s = RollupSummary(
        generated_at=_NOW,
        window_days=7,
        open_findings_total=0,
        open_findings_by_scope={},
        open_findings_by_severity={},
        open_incidents=0,
        pending_approvals=0,
    )
    with pytest.raises(Exception):
        s.open_findings_total = 99  # type: ignore[misc]
