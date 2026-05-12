"""Dependency audit — scan pyproject.toml / requirements.txt for risk.

Detects packages that ship without version constraints (any future
release gets pulled in), wildcard ranges, and pre-release pins. CVE
lookup is deliberately deferred to v2 — keeps the bot self-contained
without a third-party vulnerability-feed dependency.

Rules:
  - ``unpinned_dependency``     — no version specifier at all (high)
  - ``wildcard_dependency``     — ``*`` or unbounded ``>=`` (medium)
  - ``pre_release_pin``         — pinned to alpha/beta/rc/dev (low)
"""

from .audit import AuditResult, sweep
from .checks import Dependency, evaluate
from .parsers import parse_pyproject, parse_requirements

__all__ = [
    "AuditResult",
    "Dependency",
    "evaluate",
    "parse_pyproject",
    "parse_requirements",
    "sweep",
]
