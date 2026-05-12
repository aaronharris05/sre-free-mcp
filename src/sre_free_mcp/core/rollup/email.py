"""Email rendering for the weekly rollup."""

from __future__ import annotations

import html as _html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .audit import RollupSummary


def build_subject(summary: "RollupSummary") -> str:
    """Subject line for the weekly rollup email."""
    date = summary.generated_at.strftime("%Y-%m-%d")
    if summary.open_findings_total == 0 and summary.open_incidents == 0:
        return f"[SRE rollup] All clear — {date}"
    return (
        f"[SRE rollup] {summary.open_findings_total} open findings, "
        f"{summary.open_incidents} open incidents — {date}"
    )


def build_bodies(summary: "RollupSummary", *, project: str) -> tuple[str, str]:
    """Return ``(text_body, html_body)``."""
    return _render_text(summary, project), _render_html(summary, project)


def _render_text(summary: "RollupSummary", project: str) -> str:
    lines: list[str] = [
        f"Weekly SRE rollup — {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}",
        f"Project: {project}",
        f"Window: last {summary.window_days} days",
        "",
        f"Open findings: {summary.open_findings_total}",
        f"Open incidents: {summary.open_incidents}",
        f"Pending approvals: {summary.pending_approvals}",
        "",
        "By severity",
        "-----------",
    ]
    if summary.open_findings_by_severity:
        for sev in ("critical", "high", "medium", "low"):
            n = summary.open_findings_by_severity.get(sev, 0)
            if n:
                lines.append(f"  {sev:<10} {n}")
    else:
        lines.append("  (none)")
    lines += ["", "By scope", "--------"]
    if summary.open_findings_by_scope:
        for scope, n in sorted(
            summary.open_findings_by_scope.items(), key=lambda kv: -kv[1]
        ):
            lines.append(f"  {scope:<20} {n}")
    else:
        lines.append("  (none)")
    lines += ["", "Top findings", "------------"]
    if summary.top_findings:
        for f in summary.top_findings:
            lines.append(
                f"  {f['severity']:<10} {f['scope']:<14} "
                f"{f['scope_id']:<32} {f['gap_kind']}"
            )
    else:
        lines.append("  (none)")
    return "\n".join(lines)


def _render_html(summary: "RollupSummary", project: str) -> str:
    def _esc(value: object) -> str:
        return _html.escape(str(value))

    sev_rows = "".join(
        f"<tr><td>{_esc(sev)}</td><td style='text-align:right'>{n}</td></tr>"
        for sev, n in (
            (s, summary.open_findings_by_severity.get(s, 0))
            for s in ("critical", "high", "medium", "low")
        )
        if n
    )
    scope_rows = "".join(
        f"<tr><td>{_esc(scope)}</td><td style='text-align:right'>{n}</td></tr>"
        for scope, n in sorted(
            summary.open_findings_by_scope.items(), key=lambda kv: -kv[1]
        )
    )
    top_rows = "".join(
        f"<tr><td>{_esc(f['severity'])}</td>"
        f"<td>{_esc(f['scope'])}</td>"
        f"<td>{_esc(f['scope_id'])}</td>"
        f"<td>{_esc(f['gap_kind'])}</td></tr>"
        for f in summary.top_findings
    )
    return (
        f"<h2>Weekly SRE rollup — {summary.generated_at.strftime('%Y-%m-%d %H:%M UTC')}</h2>"
        f"<p><b>Project:</b> {_esc(project)}<br>"
        f"<b>Window:</b> last {summary.window_days} days</p>"
        f"<p><b>Open findings:</b> {summary.open_findings_total}<br>"
        f"<b>Open incidents:</b> {summary.open_incidents}<br>"
        f"<b>Pending approvals:</b> {summary.pending_approvals}</p>"
        f"<h3>By severity</h3>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<thead><tr><th>Severity</th><th>Count</th></tr></thead>"
        f"<tbody>{sev_rows or '<tr><td colspan=2>(none)</td></tr>'}</tbody>"
        f"</table>"
        f"<h3>By scope</h3>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<thead><tr><th>Scope</th><th>Count</th></tr></thead>"
        f"<tbody>{scope_rows or '<tr><td colspan=2>(none)</td></tr>'}</tbody>"
        f"</table>"
        f"<h3>Top findings</h3>"
        f"<table border='1' cellpadding='4' cellspacing='0'>"
        f"<thead><tr><th>Severity</th><th>Scope</th><th>Scope ID</th><th>Gap kind</th></tr></thead>"
        f"<tbody>{top_rows or '<tr><td colspan=4>(none)</td></tr>'}</tbody>"
        f"</table>"
    )


__all__ = ["build_bodies", "build_subject"]
