"""Pure-logic test-coverage rules."""

from __future__ import annotations

from dataclasses import dataclass, field

from sre_free_mcp.core.models import Finding


@dataclass(frozen=True)
class FileCoverage:
    """One file's coverage stats."""

    filename: str
    line_rate: float           # 0.0–1.0
    lines_valid: int


@dataclass(frozen=True)
class CoverageSnapshot:
    """A single coverage.xml worth of evidence."""

    source: str                 # path / report identifier
    overall_line_rate: float    # 0.0–1.0
    files: list[FileCoverage] = field(default_factory=list)


def evaluate(
    snapshot: CoverageSnapshot,
    *,
    overall_threshold: float = 0.80,
    module_threshold: float = 0.60,
    min_lines_for_module_check: int = 20,
) -> list[Finding]:
    """Apply coverage rules to one snapshot."""
    findings: list[Finding] = []
    base = {"source": snapshot.source}

    if snapshot.overall_line_rate < overall_threshold:
        findings.append(
            Finding(
                scope="test_coverage",
                scope_id=snapshot.source,
                gap_kind="low_overall_coverage",
                severity="high",
                details={
                    **base,
                    "overall_line_rate": snapshot.overall_line_rate,
                    "threshold": overall_threshold,
                    "rule": f"overall line coverage < {overall_threshold:.0%}",
                },
            )
        )

    for f in snapshot.files:
        # Skip tiny files where one missed line tanks the percentage.
        if f.lines_valid < min_lines_for_module_check:
            continue
        if f.line_rate < module_threshold:
            findings.append(
                Finding(
                    scope="test_coverage",
                    scope_id=f"{snapshot.source}:{f.filename}",
                    gap_kind="low_module_coverage",
                    severity="medium",
                    details={
                        **base,
                        "filename": f.filename,
                        "line_rate": f.line_rate,
                        "lines_valid": f.lines_valid,
                        "threshold": module_threshold,
                    },
                )
            )

    return findings


__all__ = ["CoverageSnapshot", "FileCoverage", "evaluate"]
