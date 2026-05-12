"""Pure-logic dependency rules."""

from __future__ import annotations

from dataclasses import dataclass, replace

from sre_free_mcp.core.models import Finding


@dataclass(frozen=True)
class Dependency:
    """One row from a parsed dependency file."""

    name: str
    specifier: str           # PEP 440 specifier, e.g. ">=2.7,<3"; "" = unpinned
    source: str = ""          # path to pyproject.toml / requirements.txt
    group: str | None = None  # optional-dependency group, if applicable

    def _replace_group(self, group: str) -> "Dependency":
        return replace(self, group=group)


_PRE_RELEASE_TOKENS = ("a", "b", "rc", "alpha", "beta", "dev")


def evaluate(dep: Dependency) -> list[Finding]:
    """Apply rules to one dependency line."""
    findings: list[Finding] = []
    base = {
        "package": dep.name,
        "specifier": dep.specifier,
        "source": dep.source,
        "group": dep.group,
    }

    spec = (dep.specifier or "").strip()

    if not spec:
        findings.append(
            Finding(
                scope="dependency",
                scope_id=f"{dep.source}:{dep.name}",
                gap_kind="unpinned_dependency",
                severity="high",
                details={
                    **base,
                    "rule": "no version specifier — any future release "
                    "may break the build or introduce vulnerabilities",
                },
            )
        )
        return findings

    if "*" in spec or _is_unbounded_lower(spec):
        findings.append(
            Finding(
                scope="dependency",
                scope_id=f"{dep.source}:{dep.name}",
                gap_kind="wildcard_dependency",
                severity="medium",
                details={
                    **base,
                    "rule": "wildcard / unbounded range — pin an upper bound to "
                    "control major-version risk",
                },
            )
        )

    if _is_pre_release_pin(spec):
        findings.append(
            Finding(
                scope="dependency",
                scope_id=f"{dep.source}:{dep.name}",
                gap_kind="pre_release_pin",
                severity="low",
                details={
                    **base,
                    "rule": "pinned to a pre-release; not stable for production",
                },
            )
        )

    return findings


def _is_unbounded_lower(spec: str) -> bool:
    """``>=X`` with no ``<`` upper bound. Each clause split on comma."""
    has_lower = False
    has_upper = False
    for clause in spec.split(","):
        clause = clause.strip()
        if clause.startswith(">=") or clause.startswith(">"):
            has_lower = True
        elif clause.startswith("<=") or clause.startswith("<"):
            has_upper = True
    return has_lower and not has_upper


def _is_pre_release_pin(spec: str) -> bool:
    """Detect ``==1.0.0a1`` / ``==1.0rc2`` style pins."""
    if "==" not in spec:
        return False
    # The version segment after `==`. Could have multiple `==` clauses
    # but the typical case is one.
    for clause in spec.split(","):
        clause = clause.strip()
        if clause.startswith("==") or clause.startswith("==="):
            version = clause.lstrip("=").strip()
            lower = version.lower()
            for tok in _PRE_RELEASE_TOKENS:
                if tok in lower:
                    return True
    return False


__all__ = ["Dependency", "evaluate"]
