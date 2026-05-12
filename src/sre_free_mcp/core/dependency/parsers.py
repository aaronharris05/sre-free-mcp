"""Parse dependency declarations from pyproject.toml / requirements.txt.

Both produce a list of :class:`Dependency` records that
:mod:`checks.evaluate` operates on.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

from .checks import Dependency


_REQ_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9_\-.]+)"          # package name
    r"\s*(?P<extras>\[[A-Za-z0-9_,\- ]+\])?"   # extras (ignored)
    r"\s*(?P<spec>[<>=!~].+)?\s*$"             # version specifier (optional)
)


def parse_pyproject(path: Path) -> list[Dependency]:
    """Read ``[project].dependencies`` + ``[project.optional-dependencies]``.

    Falls back to the poetry / hatch sections if ``[project]`` is absent.
    """
    text = path.read_text(encoding="utf-8")
    data = tomllib.loads(text)

    out: list[Dependency] = []
    project = data.get("project", {})
    for raw in project.get("dependencies", []):
        dep = _parse_pep508(raw)
        if dep is not None:
            out.append(dep)
    for group, deps in (project.get("optional-dependencies", {}) or {}).items():
        for raw in deps:
            dep = _parse_pep508(raw)
            if dep is not None:
                out.append(dep._replace_group(group))

    # Poetry fallback.
    poetry = data.get("tool", {}).get("poetry", {})
    for name, spec in (poetry.get("dependencies", {}) or {}).items():
        if isinstance(spec, str):
            out.append(Dependency(name=name, specifier=spec, source=str(path)))
        elif isinstance(spec, dict) and "version" in spec:
            out.append(Dependency(name=name, specifier=spec["version"], source=str(path)))
        else:
            out.append(Dependency(name=name, specifier="", source=str(path)))

    return out


def parse_requirements(path: Path) -> list[Dependency]:
    """Read a pip-style ``requirements.txt``. Comments + blank lines + ``-r``
    recursive includes are skipped."""
    out: list[Dependency] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or line.startswith("-"):
            continue
        m = _REQ_RE.match(line)
        if not m:
            continue
        out.append(
            Dependency(
                name=m.group("name"),
                specifier=(m.group("spec") or "").strip(),
                source=str(path),
            )
        )
    return out


def _parse_pep508(raw: str) -> Dependency | None:
    """Parse a PEP 508 string like ``'pydantic>=2.7'`` into a Dependency."""
    # Strip environment markers (``foo; python_version>='3.10'``); we
    # don't audit on markers.
    raw = raw.split(";", 1)[0].strip()
    if not raw:
        return None
    m = _REQ_RE.match(raw)
    if not m:
        return None
    return Dependency(
        name=m.group("name"),
        specifier=(m.group("spec") or "").strip(),
        source="",
    )


__all__ = ["parse_pyproject", "parse_requirements"]
