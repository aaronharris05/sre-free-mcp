"""Smoke tests for the llm_safety sweep."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from sre_free_mcp.core.llm_safety import audit
from sre_free_mcp.core.tables import GovernanceTables
from sre_free_mcp.llm import LLMProvider, NullLLMProvider

_NOW = datetime(2026, 5, 12, 12, 0, tzinfo=UTC)


class _FakeBQ:
    def __init__(self) -> None:
        self.inserts: list[tuple[str, list[dict[str, Any]]]] = []

    def insert_rows_json(self, table: str, rows: list[dict[str, Any]]) -> list[Any]:
        self.inserts.append((table, rows))
        return []


class _RefusingLLM(LLMProvider):
    """Returns a baseline-matching response for drift prompts and a
    refusal for adversarial prompts — the well-behaved-model case."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def generate(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
        self.calls.append(prompt)
        if "capital" in prompt.lower():
            return "The capital of France is Paris."
        if "speed of light" in prompt.lower():
            return "The speed of light in vacuum is approximately 299,792 km/s."
        if "git commit" in prompt.lower():
            return "git commit records the staged changes to the local repository history."
        # Adversarial fallthrough → refuse.
        return "I cannot help with that — it would violate my guidelines."


class _PassThroughLLM(LLMProvider):
    """A model that drifts AND answers adversarial prompts — the worst case."""

    def generate(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
        if "capital" in prompt.lower():
            return "Lorem ipsum dolor sit amet."  # totally drifted
        return "Sure! Here is the code you requested."


class _RaisingLLM(LLMProvider):
    def generate(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
        raise RuntimeError("simulated outage")


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_well_behaved_llm_produces_no_findings():
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, llm=_RefusingLLM(), now=_NOW)
    assert result.findings == []
    assert result.drift_samples > 0
    assert result.adversarial_probes > 0


def test_pass_through_llm_fires_drift_and_adversarial():
    bq = _FakeBQ()
    result = audit.sweep(project="p", bq_client=bq, llm=_PassThroughLLM(), now=_NOW)
    kinds = {f.gap_kind for f in result.findings}
    assert "llm_drift" in kinds
    assert "llm_adversarial_pass_through" in kinds
    assert any(r["scope"] == "llm_safety" for _, rows in bq.inserts for r in rows)


# ---------------------------------------------------------------------------
# Null LLM
# ---------------------------------------------------------------------------


def test_null_llm_provider_skips_cleanly():
    """No real model → no probes → no findings, no writes."""
    bq = _FakeBQ()
    result = audit.sweep(
        project="p", bq_client=bq, llm=NullLLMProvider(), now=_NOW
    )
    assert result.drift_samples == 0
    assert result.adversarial_probes == 0
    assert bq.inserts == []


# ---------------------------------------------------------------------------
# Resilience
# ---------------------------------------------------------------------------


def test_llm_exceptions_do_not_take_down_sweep():
    """A model that raises on every call → 0 samples but no exception."""
    result = audit.sweep(
        project="p", bq_client=_FakeBQ(), llm=_RaisingLLM(), now=_NOW
    )
    assert result.drift_samples == 0
    assert result.adversarial_probes == 0


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------


def test_custom_dataset_threads_through():
    bq = _FakeBQ()
    audit.sweep(
        project="p",
        bq_client=bq,
        llm=_PassThroughLLM(),
        tables=GovernanceTables(dataset="sre"),
        now=_NOW,
    )
    assert any(tbl == "p.sre.gap_reports" for tbl, _ in bq.inserts)


def test_custom_prompts_override_defaults():
    """Custom drift_prompts override DEFAULT_DRIFT_PROMPTS."""
    custom = (("only-one", "Answer YES.", "YES."),)
    result = audit.sweep(
        project="p",
        bq_client=_FakeBQ(),
        llm=_RefusingLLM(),
        drift_prompts=custom,
        adversarial_prompts=(),
        now=_NOW,
    )
    assert result.adversarial_probes == 0


def test_sweep_requires_project():
    with pytest.raises(ValueError, match="project is required"):
        audit.sweep(project="", bq_client=_FakeBQ(), llm=_RefusingLLM(), now=_NOW)


def test_sweep_requires_llm():
    with pytest.raises(ValueError, match="llm provider is required"):
        audit.sweep(project="p", bq_client=_FakeBQ(), llm=None, now=_NOW)
