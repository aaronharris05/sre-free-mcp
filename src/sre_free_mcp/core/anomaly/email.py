"""Build the daily anomaly email (subject + text + HTML).

One email per ``owner_team`` per sweep. The runner groups findings by
routed team and calls :func:`build_bodies` once per team.

The LLM-driven narrative is best-effort: if the configured provider is
:class:`NullLLMProvider` (or any provider returning the null sentinel),
the email falls back to a deterministic summary line so the email still
makes sense without a live model call.
"""

from __future__ import annotations

import html
import json
import logging
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sre_free_mcp.core.models import Finding
from sre_free_mcp.llm import NULL_LLM_SENTINEL, LLMProvider, NullLLMProvider

logger = logging.getLogger(__name__)

_SEV_RANK = {"critical": 4, "high": 3, "medium": 2, "low": 1}


# ---------------------------------------------------------------------------
# Subject
# ---------------------------------------------------------------------------


def build_subject(
    findings: list[Finding],
    generated_at: datetime,
    *,
    owner_team: str | None = None,
) -> str:
    """Format the email subject."""
    date = generated_at.strftime("%Y-%m-%d")
    suffix = f" [{owner_team}]" if owner_team else ""
    if not findings:
        return f"[Anomaly] All clear — {date}{suffix}"
    high = sum(1 for f in findings if f.severity == "high")
    if high:
        return f"[Anomaly] {high} high / {len(findings)} total — {date}{suffix}"
    return f"[Anomaly] {len(findings)} findings — {date}{suffix}"


# ---------------------------------------------------------------------------
# Bodies
# ---------------------------------------------------------------------------


def build_bodies(
    findings: list[Finding],
    generated_at: datetime,
    *,
    project: str,
    owner_team: str | None = None,
    llm: LLMProvider | None = None,
    gap_reports_table: str = "governance.gap_reports",
) -> tuple[str, str]:
    """Return ``(text_body, html_body)`` for the email."""
    counts = _aggregate(findings)
    narrative = _generate_narrative(findings, counts, llm=llm)
    text = _render_text(findings, counts, narrative, generated_at, project, owner_team, gap_reports_table)
    html_body = _render_html(findings, counts, narrative, generated_at, project, owner_team, gap_reports_table)
    return text, html_body


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _abs_z(f: Finding) -> float:
    try:
        return abs(float((f.details or {}).get("z_score") or 0))
    except (TypeError, ValueError):
        return 0.0


def _aggregate(findings: list[Finding]) -> dict[str, Any]:
    by_table: defaultdict[str, list[Finding]] = defaultdict(list)
    by_severity = Counter(f.severity for f in findings)
    for f in findings:
        table = (f.details or {}).get("table") or f.scope_id.split(":", 1)[0]
        by_table[table].append(f)
    by_table_sorted = sorted(
        by_table.items(),
        key=lambda kv: -max((_abs_z(f) for f in kv[1]), default=0.0),
    )
    return {
        "total": len(findings),
        "by_severity": dict(by_severity),
        "by_table": [
            {
                "table": tbl,
                "findings": sorted(fs, key=lambda f: -_abs_z(f)),
                "worst_z": max((_abs_z(f) for f in fs), default=0.0),
            }
            for tbl, fs in by_table_sorted
        ],
    }


# ---------------------------------------------------------------------------
# Narrative (LLM-backed, with deterministic fallback)
# ---------------------------------------------------------------------------


_NARRATIVE_PROMPT = """
You are summarizing a daily data-anomaly audit for an on-call operator.
Write 2-4 plain-text sentences for the email body that:
  - Open with the highest |z| anomaly: name the table.column and the
    timestamp it landed at (use the sample for these; do not invent
    values).
  - State the count by severity and which tables were affected.
  - End with a recommended next-step query: a SELECT against the
    affected table for the timestamp range that contains the worst
    anomaly. Cite the table.column and the as_of timestamp from the
    sample; do not invent column names.

No emoji, no markdown, no preamble like "Here is the summary".

Findings summary:
{summary}

Sample findings (use as flavor; do not enumerate):
{sample}
""".strip()


def _generate_narrative(
    findings: list[Finding],
    counts: dict[str, Any],
    *,
    llm: LLMProvider | None,
) -> str:
    if not findings:
        return "No data anomalies today."
    fallback = _fallback_narrative(counts)
    if llm is None or isinstance(llm, NullLLMProvider):
        return fallback

    summary = {
        "total": counts["total"],
        "by_severity": counts["by_severity"],
        "tables": [
            {"table": k["table"], "count": len(k["findings"]), "worst_z": k["worst_z"]}
            for k in counts["by_table"][:5]
        ],
    }
    sample = [
        {
            "severity": f.severity,
            "table": (f.details or {}).get("table"),
            "metric": (f.details or {}).get("metric_column"),
            "z_score": (f.details or {}).get("z_score"),
            "value": (f.details or {}).get("value"),
            "as_of": (f.details or {}).get("as_of"),
        }
        for f in sorted(findings, key=lambda f: (-_SEV_RANK.get(f.severity, 0), -_abs_z(f)))[:10]
    ]
    prompt = _NARRATIVE_PROMPT.format(
        summary=json.dumps(summary, indent=2, default=str),
        sample=json.dumps(sample, indent=2, default=str),
    )
    try:
        out = llm.generate(prompt, max_tokens=400, temperature=0.2)
        if NULL_LLM_SENTINEL in out:
            return fallback
        return out.strip() or fallback
    except Exception:
        logger.exception("anomaly_email_narrative_failed; using deterministic fallback")
        return fallback


def _fallback_narrative(counts: dict[str, Any]) -> str:
    parts = [f"{counts['total']} data anomalies today."]
    sev = counts["by_severity"]
    if sev.get("high"):
        parts.append(f"{sev['high']} high severity.")
    if counts["by_table"]:
        top = counts["by_table"][0]
        parts.append(f"Worst table: {top['table']} (worst |z|={top['worst_z']:.1f}).")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------


def _render_text(
    findings: list[Finding],
    counts: dict[str, Any],
    narrative: str,
    generated_at: datetime,
    project: str,
    owner_team: str | None,
    gap_reports_table: str,
) -> str:
    lines = [
        f"Anomaly audit — {generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Project: {project}",
    ]
    if owner_team:
        lines.append(f"Routed to: {owner_team}")
    lines.extend(["", narrative, "", "Findings", "--------"])
    if not findings:
        lines.append("  (none)")
    else:
        for k in counts["by_table"]:
            lines.append(f"\n  [{k['table']}] {len(k['findings'])} finding(s)")
            for f in k["findings"]:
                d = f.details or {}
                z = d.get("z_score")
                z_str = f"|z|={abs(float(z)):.1f}" if isinstance(z, (int, float)) else "|z|=n/a"
                value = d.get("value")
                value_str = f"{float(value):.2f}" if isinstance(value, (int, float)) else "?"
                lines.append(
                    f"    {f.severity:<6} {d.get('metric_column', '?'):<20} "
                    f"{z_str}  value={value_str}  as_of={d.get('as_of', '?')}"
                )
    lines.extend(
        [
            "",
            f"Detail: SELECT * FROM `{project}.{gap_reports_table}` "
            f"WHERE generated_at = TIMESTAMP('{generated_at.isoformat()}') "
            "AND scope = 'data' ORDER BY severity DESC",
        ]
    )
    return "\n".join(lines)


def _render_html(
    findings: list[Finding],
    counts: dict[str, Any],
    narrative: str,
    generated_at: datetime,
    project: str,
    owner_team: str | None,
    gap_reports_table: str,
) -> str:
    routed = f"<p><b>Routed to:</b> {html.escape(owner_team)}</p>" if owner_team else ""
    rows_html = ""
    if findings:
        rows_html = "\n".join(
            f"<tr><td>{html.escape(k['table'])}</td>"
            f"<td>{len(k['findings'])}</td>"
            f"<td>{k['worst_z']:.1f}</td></tr>"
            for k in counts["by_table"]
        )
        table = (
            "<table border='1' cellpadding='6' cellspacing='0'>"
            "<thead><tr><th>Table</th><th># findings</th><th>worst |z|</th></tr></thead>"
            f"<tbody>{rows_html}</tbody></table>"
        )
    else:
        table = "<p>No findings.</p>"
    return (
        f"<h2>Anomaly audit — {generated_at.strftime('%Y-%m-%d %H:%M UTC')}</h2>"
        f"<p><b>Project:</b> {html.escape(project)}</p>"
        f"{routed}"
        f"<p>{html.escape(narrative)}</p>"
        f"{table}"
        f"<p style='color:#666;font-size:11px;margin-top:18px'>"
        f"Detail: <code>SELECT * FROM `{html.escape(project)}.{html.escape(gap_reports_table)}` "
        f"WHERE generated_at = TIMESTAMP('{generated_at.isoformat()}') "
        f"AND scope = 'data' ORDER BY severity DESC</code></p>"
    )


__all__ = ["build_bodies", "build_subject"]
