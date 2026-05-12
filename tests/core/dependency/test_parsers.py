"""Unit tests for the dependency parsers."""

from __future__ import annotations

from pathlib import Path

import pytest

from sre_free_mcp.core.dependency.parsers import parse_pyproject, parse_requirements


def _write(p: Path, content: str) -> Path:
    p.write_text(content, encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# parse_pyproject — PEP 621 [project] section
# ---------------------------------------------------------------------------


def test_parses_pep621_dependencies(tmp_path: Path):
    p = _write(
        tmp_path / "pyproject.toml",
        """
[project]
name = "x"
version = "0.1.0"
dependencies = [
    "pydantic>=2.7",
    "pyyaml",
    "click>=8.1,<9",
]
""",
    )
    deps = parse_pyproject(p)
    names = {d.name for d in deps}
    assert names == {"pydantic", "pyyaml", "click"}


def test_parses_pep621_optional_dependencies_with_group(tmp_path: Path):
    p = _write(
        tmp_path / "pyproject.toml",
        """
[project]
name = "x"
dependencies = []

[project.optional-dependencies]
dev = ["pytest>=8.0"]
docs = ["sphinx"]
""",
    )
    deps = parse_pyproject(p)
    groups = {d.name: d.group for d in deps}
    assert groups == {"pytest": "dev", "sphinx": "docs"}


def test_parses_pep621_strips_environment_markers(tmp_path: Path):
    """Markers like ``; python_version>='3.10'`` should not break parsing."""
    p = _write(
        tmp_path / "pyproject.toml",
        """
[project]
name = "x"
dependencies = ["tomli; python_version<'3.11'"]
""",
    )
    deps = parse_pyproject(p)
    assert len(deps) == 1
    assert deps[0].name == "tomli"


def test_falls_back_to_poetry_section(tmp_path: Path):
    p = _write(
        tmp_path / "pyproject.toml",
        """
[tool.poetry]
name = "x"
version = "0.1.0"

[tool.poetry.dependencies]
python = "^3.12"
requests = "^2.31"
django = { version = ">=5.0,<6", python = ">=3.12" }
""",
    )
    deps = parse_pyproject(p)
    names = {d.name for d in deps}
    assert {"python", "requests", "django"}.issubset(names)


# ---------------------------------------------------------------------------
# parse_requirements
# ---------------------------------------------------------------------------


def test_parses_requirements_skipping_comments_and_blanks(tmp_path: Path):
    p = _write(
        tmp_path / "requirements.txt",
        """
# top comment

pydantic>=2.7
pyyaml  # inline comment
click>=8.1,<9
""",
    )
    deps = parse_requirements(p)
    assert {d.name for d in deps} == {"pydantic", "pyyaml", "click"}


def test_parses_requirements_skips_recursive_include(tmp_path: Path):
    p = _write(
        tmp_path / "requirements.txt",
        """
-r requirements-base.txt
flask
""",
    )
    deps = parse_requirements(p)
    assert [d.name for d in deps] == ["flask"]


def test_parses_requirements_captures_specifier(tmp_path: Path):
    p = _write(tmp_path / "requirements.txt", "django>=5.0,<6\n")
    deps = parse_requirements(p)
    assert deps[0].specifier == ">=5.0,<6"


def test_parses_requirements_unpinned(tmp_path: Path):
    p = _write(tmp_path / "requirements.txt", "flask\n")
    deps = parse_requirements(p)
    assert deps[0].specifier == ""
