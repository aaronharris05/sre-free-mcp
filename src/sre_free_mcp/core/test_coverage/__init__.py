"""Test-coverage audit — parse Cobertura XML reports and flag under-tested modules.

Cobertura is the de-facto coverage format for Python (pytest-cov,
coverage.py) plus most Java / .NET tooling, so the same parser works
across most stacks.

Rules:
  - ``low_overall_coverage``  — total line coverage below threshold (high)
  - ``low_module_coverage``   — one file below threshold (medium)
  - ``regressed_coverage``    — file coverage dropped vs prior run (high)
                                — deferred to v2 (needs historical store)
"""

from .audit import AuditResult, sweep
from .checks import CoverageSnapshot, FileCoverage, evaluate
from .parser import parse_cobertura

__all__ = [
    "AuditResult",
    "CoverageSnapshot",
    "FileCoverage",
    "evaluate",
    "parse_cobertura",
    "sweep",
]
