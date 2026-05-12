"""Smoke tests for the RCA narrative sweep."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.rca import audit
from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.llm import LLMProvider, NullLLMProvider

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.queries: list[tuple[str, list[Any]]] = []

    def query(self, sql: str, job_config: Any | None = None) -> "_FakeJob":
        params = list(getattr(job_config, "query_parameters", None) or [])
        self.queries.append((sql, params))
        return _FakeJob()

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        return []


class _FakeJob:
    def result(self) -> list[Any]:
        return []


class _FakeLLM(LLMProvider):
    def __init__(self, response: str = "narrative-under-test") -> None:
        self.response = response
        self.calls: list[str] = []

    def generate(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2
    ) -> str:
        self.calls.append(prompt)
        return self.response


class _RaisingLLM(LLMProvider):
    def generate(
        self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2
    ) -> str:
        raise RuntimeError("simulated outage")


def _patch_reads(
    monkeypatch,
    *,
    pending: list[dict[str, Any]],
    events: list[dict[str, Any]] | None = None,
    findings: list[dict[str, Any]] | None = None,
):
    monkeypatch.setattr(audit, "_read_pending", lambda *a, **kw: list(pending))
    monkeypatch.setattr(audit, "_recent_events", lambda *a, **kw: list(events or []))
    monkeypatch.setattr(audit, "_open_findings", lambda *a, **kw: list(findings or []))


def _item(
    *,
    id_: str = "ap-1",
    workflow: str | None = "wf_a",
    narrative: str | None = None,
) -> dict[str, Any]:
    return {
        "id": id_,
        "action_kind": "retry_exhausted",
        "action_payload": json.dumps({"workflow_name": workflow, "attempts": 3}),
        "narrative": narrative,
        "requested_at": _NOW,
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_sweep_generates_narrative_for_pending_item(monkeypatch):
    _patch_reads(monkeypatch, pending=[_item()])
    llm = _FakeLLM(response="Cloud Run quota exhaustion likely.")
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, llm=llm, now=_NOW)
    assert result.pending == 1
    assert result.generated == 1
    assert len(llm.calls) == 1


def test_sweep_writes_back_to_approval_queue(monkeypatch):
    _patch_reads(monkeypatch, pending=[_item()])
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        llm=_FakeLLM("narrative"),
        now=_NOW,
    )
    update_sqls = [sql for sql, _ in bq.queries if "UPDATE" in sql]
    assert len(update_sqls) == 1
    assert "approval_queue" in update_sqls[0]


def test_sweep_prompt_includes_action_and_evidence(monkeypatch):
    _patch_reads(
        monkeypatch,
        pending=[_item()],
        events=[
            {
                "occurred_at": _NOW,
                "event_kind": "sre_retry_attempt",
                "payload": json.dumps({"outcome": "trigger_failed"}),
                "status": "blocked",
            }
        ],
        findings=[
            {
                "generated_at": _NOW,
                "scope": "workflow",
                "gap_kind": "workflow_missed_run",
                "severity": "high",
                "details": json.dumps({"age_hours": 30}),
            }
        ],
    )
    llm = _FakeLLM()
    bq = _FakeBQ()
    audit.sweep(project="p", bq_client=bq, llm=llm, now=_NOW)
    prompt = llm.calls[0]
    assert "retry_exhausted" in prompt
    assert "wf_a" in prompt
    assert "workflow_missed_run" in prompt


# ---------------------------------------------------------------------------
# Skipping
# ---------------------------------------------------------------------------


def test_sweep_skips_items_with_existing_narrative(monkeypatch):
    _patch_reads(
        monkeypatch,
        pending=[_item(narrative="operator-edited narrative")],
    )
    llm = _FakeLLM()
    result = audit.sweep(project="p", bq_client=_FakeBQ(), llm=llm, now=_NOW)
    assert result.generated == 0
    assert result.skipped_existing == 1
    assert llm.calls == []


def test_sweep_with_null_llm_does_not_call_or_write(monkeypatch):
    _patch_reads(monkeypatch, pending=[_item()])
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, llm=NullLLMProvider(), now=_NOW
    )
    assert result.generated == 0
    assert result.skipped_null_llm == 1
    # No UPDATE issued.
    assert all("UPDATE" not in sql for sql, _ in bq.queries)


def test_sweep_skips_when_llm_returns_sentinel(monkeypatch):
    """A custom LLM returning the null sentinel is treated as 'no answer'."""
    from sre_free_mcp.llm import NULL_LLM_SENTINEL

    class _SentinelLLM(LLMProvider):
        def generate(self, prompt, *, max_tokens=1024, temperature=0.2):
            return f"{NULL_LLM_SENTINEL} prompt_len=99"

    _patch_reads(monkeypatch, pending=[_item()])
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), llm=_SentinelLLM(), now=_NOW
    )
    assert result.skipped_null_llm == 1


def test_sweep_continues_when_llm_raises(monkeypatch):
    """One bad LLM call doesn't take the rest of the queue down."""
    _patch_reads(monkeypatch, pending=[_item(id_="a"), _item(id_="b")])
    llm = _RaisingLLM()
    result = audit.sweep(project="p", bq_client=_FakeBQ(), llm=llm, now=_NOW)
    assert result.generated == 0
    assert result.pending == 2


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_sweep_no_pending_items_returns_zero(monkeypatch):
    _patch_reads(monkeypatch, pending=[])
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), llm=_FakeLLM(), now=_NOW
    )
    assert result.pending == 0
    assert result.generated == 0


def test_sweep_uses_custom_dataset(monkeypatch):
    _patch_reads(monkeypatch, pending=[_item()])
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        llm=_FakeLLM(),
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any("p.sre.approval_queue" in sql for sql, _ in bq.queries)


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), llm=_FakeLLM(), now=_NOW)


def test_sweep_requires_llm(monkeypatch):
    with pytest.raises(ValueError, match="llm provider is required"):
        audit.sweep(project="p", bq_client=_FakeBQ(), llm=None, now=_NOW)


def test_sweep_handles_payload_without_workflow_name(monkeypatch):
    """Some action_kinds may not have a workflow_name in payload — still
    produce a finding rather than crash."""
    item = {
        "id": "x",
        "action_kind": "retry_exhausted",
        "action_payload": json.dumps({"other": "stuff"}),
        "narrative": None,
        "requested_at": _NOW,
    }
    _patch_reads(monkeypatch, pending=[item])
    llm = _FakeLLM()
    result = audit.sweep(project="p", bq_client=_FakeBQ(), llm=llm, now=_NOW)
    assert result.generated == 1
