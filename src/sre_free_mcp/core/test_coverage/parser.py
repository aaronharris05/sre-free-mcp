"""Parse Cobertura coverage.xml reports.

Cobertura schema (simplified):

    <coverage line-rate="0.85" timestamp="..." ...>
      <packages>
        <package name="..." line-rate="0.9">
          <classes>
            <class name="..." filename="x.py" line-rate="0.85">
              <lines>...</lines>
            </class>
          </classes>
        </package>
      </packages>
    </coverage>

We care about: the top-level ``line-rate`` (overall) and per-``class``
``line-rate`` + ``filename`` (per-file). Packages are aggregated by
parser callers, not us.
"""

from __future__ import annotations

import logging
from pathlib import Path
from xml.etree import ElementTree as ET

from .checks import CoverageSnapshot, FileCoverage

logger = logging.getLogger(__name__)


def parse_cobertura(path: Path) -> CoverageSnapshot:
    """Read one coverage.xml and return a :class:`CoverageSnapshot`."""
    text = path.read_text(encoding="utf-8")
    root = ET.fromstring(text)

    overall = _safe_float(root.attrib.get("line-rate"))
    files: list[FileCoverage] = []
    for cls in root.iter("class"):
        filename = cls.attrib.get("filename") or cls.attrib.get("name")
        if not filename:
            continue
        line_rate = _safe_float(cls.attrib.get("line-rate"))
        lines_valid = int(cls.attrib.get("lines-valid") or 0) or _count_lines(cls)
        if lines_valid == 0:
            continue  # empty / generated file
        files.append(
            FileCoverage(
                filename=filename,
                line_rate=line_rate,
                lines_valid=lines_valid,
            )
        )
    return CoverageSnapshot(
        source=str(path),
        overall_line_rate=overall,
        files=files,
    )


def _safe_float(value: str | None) -> float:
    try:
        return float(value) if value is not None else 0.0
    except ValueError:
        return 0.0


def _count_lines(class_node: ET.Element) -> int:
    """Fallback line count when ``lines-valid`` attribute is missing."""
    return sum(1 for _ in class_node.iter("line"))


__all__ = ["parse_cobertura"]
