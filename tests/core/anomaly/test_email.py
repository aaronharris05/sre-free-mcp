"""Smoke tests for the anomaly email builder."""

from __future__ import annotations

from datetime import UTC, datetime

from sre_free_mcp.core.anomaly.email import build_bodies, build_subject
from sre_free_mcp.core.models import Finding
from sre_free_mcp.llm import NULL_LLM_SENTINEL, LLMProvider, NullLLMProvider

_NOW = datetime(2026, 5, 1, 4, 0, tzinfo=UTC)


def _f(
    *,
    severity: str = "high",
    table: str = "ex.api_latency",
    metric: str = "latency_p95_ms",
    z: float = 6.5,
    value: float = 142.3,
    as_of: str = "2026-05-01T03:54:00+00:00",
    owner_team: str = "data_owners",
) -> Finding:
    return Finding(
        scope="data",
        scope_id=f"{table}:{metric}",
        gap_kind="anomaly_zscore",
        severity=severity,
        details={
            "table": table,
            "metric_column": metric,
            "z_score": z,
            "value": value,
            "as_of": as_of,
            "owner_team": owner_team,
            "model": "isolation_forest",
        },
    )


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


def test_subject_no_findings():
    assert build_subject([], _NOW) == "[Anomaly] All clear — 2026-05-01"


def test_subject_with_owner_team_suffix():
    s = build_subject([_f()], _NOW, owner_team="data_owners")
    assert "[data_owners]" in s


def test_subject_high_severity_count_appears():
    findings = [_f(severity="high"), _f(severity="high"), _f(severity="medium")]
    assert "2 high / 3 total" in build_subject(findings, _NOW)


def test_subject_no_high_uses_total_only():
    findings = [_f(severity="medium"), _f(severity="medium")]
    s = build_subject(findings, _NOW)
    assert "2 findings" in s
    assert "high" not in s


# ---------------------------------------------------------------------------
# Bodies — empty
# ---------------------------------------------------------------------------


def test_bodies_empty_findings_have_no_findings_note():
    text, html_body = build_bodies([], _NOW, project="p")
    assert "No data anomalies today." in text
    assert "No findings" in html_body


# ---------------------------------------------------------------------------
# Bodies — null LLM uses fallback narrative
# ---------------------------------------------------------------------------


def test_null_llm_produces_deterministic_narrative():
    findings = [_f(severity="high", z=7.0), _f(severity="medium", z=4.0)]
    text, _ = build_bodies(findings, _NOW, project="p", llm=NullLLMProvider())
    # Fallback summary mentions count + severity + worst-table info.
    assert "2 data anomalies today" in text
    assert "1 high severity" in text


def test_no_llm_uses_fallback_narrative():
    findings = [_f()]
    text, _ = build_bodies(findings, _NOW, project="p", llm=None)
    assert "1 data anomalies today" in text


# ---------------------------------------------------------------------------
# Bodies — real LLM (fake one)
# ---------------------------------------------------------------------------


class _FakeLLM(LLMProvider):
    def __init__(self, response: str = "synthetic narrative under test") -> None:
        self.response = response
        self.calls: list[str] = []

    def generate(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
        self.calls.append(prompt)
        return self.response


class _RaisingLLM(LLMProvider):
    def generate(self, prompt: str, *, max_tokens: int = 1024, temperature: float = 0.2) -> str:
        raise RuntimeError("simulated outage")


def test_llm_narrative_appears_in_text_body():
    llm = _FakeLLM(response="The largest outlier landed at ex.api_latency.latency_p95_ms.")
    text, html_body = build_bodies([_f()], _NOW, project="p", llm=llm)
    assert "The largest outlier landed at ex.api_latency.latency_p95_ms." in text
    assert "The largest outlier landed at ex.api_latency.latency_p95_ms." in html_body
    assert len(llm.calls) == 1


def test_llm_returning_null_sentinel_falls_back():
    """If a custom LLMProvider returns the null sentinel, treat it as
    not-available and use the deterministic fallback."""
    llm = _FakeLLM(response=f"{NULL_LLM_SENTINEL} model=stub prompt_len=99")
    text, _ = build_bodies([_f()], _NOW, project="p", llm=llm)
    assert "1 data anomalies today" in text


def test_llm_exception_falls_back():
    text, _ = build_bodies([_f()], _NOW, project="p", llm=_RaisingLLM())
    assert "1 data anomalies today" in text


# ---------------------------------------------------------------------------
# Bodies — rendering details
# ---------------------------------------------------------------------------


def test_text_body_includes_drilldown_sql():
    findings = [_f()]
    text, _ = build_bodies(findings, _NOW, project="my-proj", llm=NullLLMProvider())
    assert "SELECT * FROM `my-proj.governance.gap_reports`" in text
    assert "scope = 'data'" in text


def test_custom_gap_reports_table_threads_into_drilldown():
    findings = [_f()]
    text, _ = build_bodies(
        findings,
        _NOW,
        project="my-proj",
        llm=NullLLMProvider(),
        gap_reports_table="sre.gap_reports",
    )
    assert "`my-proj.sre.gap_reports`" in text


def test_html_body_escapes_owner_team():
    """HTML rendering must escape user-controllable strings."""
    _, html_body = build_bodies(
        [_f()],
        _NOW,
        project="p",
        owner_team="<script>alert(1)</script>",
        llm=NullLLMProvider(),
    )
    assert "<script>" not in html_body
    assert "&lt;script&gt;" in html_body


def test_findings_grouped_by_table_ordered_by_worst_z():
    findings = [
        _f(table="ex.low_signal", z=3.5),
        _f(table="ex.high_signal", z=9.0),
        _f(table="ex.low_signal", z=3.6),
    ]
    text, _ = build_bodies(findings, _NOW, project="p", llm=NullLLMProvider())
    high_pos = text.find("ex.high_signal")
    low_pos = text.find("ex.low_signal")
    assert 0 <= high_pos < low_pos
