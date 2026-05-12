"""Unit tests for the Cobertura parser."""

from __future__ import annotations

from pathlib import Path

from sre_free_mcp.core.test_coverage.parser import parse_cobertura


_COBERTURA_SAMPLE = """<?xml version="1.0" ?>
<coverage line-rate="0.85" branch-rate="0.0" timestamp="1234" lines-valid="200" lines-covered="170">
  <packages>
    <package name="app" line-rate="0.9">
      <classes>
        <class filename="app/main.py" name="main.py" line-rate="0.95" lines-valid="40">
          <lines/>
        </class>
        <class filename="app/utils.py" name="utils.py" line-rate="0.40" lines-valid="50">
          <lines/>
        </class>
        <class filename="app/__init__.py" name="__init__.py" line-rate="1.0" lines-valid="0">
          <lines/>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def test_parses_overall_line_rate(tmp_path: Path):
    p = tmp_path / "coverage.xml"
    p.write_text(_COBERTURA_SAMPLE, encoding="utf-8")
    snap = parse_cobertura(p)
    assert snap.overall_line_rate == 0.85


def test_parses_per_file_coverage(tmp_path: Path):
    p = tmp_path / "coverage.xml"
    p.write_text(_COBERTURA_SAMPLE, encoding="utf-8")
    snap = parse_cobertura(p)
    by_file = {f.filename: f for f in snap.files}
    assert by_file["app/main.py"].line_rate == 0.95
    assert by_file["app/utils.py"].line_rate == 0.40
    assert by_file["app/utils.py"].lines_valid == 50


def test_skips_empty_files(tmp_path: Path):
    """A file with lines-valid=0 should be excluded."""
    p = tmp_path / "coverage.xml"
    p.write_text(_COBERTURA_SAMPLE, encoding="utf-8")
    snap = parse_cobertura(p)
    assert all(f.lines_valid > 0 for f in snap.files)
    assert "app/__init__.py" not in {f.filename for f in snap.files}


def test_parses_source_path_stored_on_snapshot(tmp_path: Path):
    p = tmp_path / "coverage.xml"
    p.write_text(_COBERTURA_SAMPLE, encoding="utf-8")
    snap = parse_cobertura(p)
    assert snap.source == str(p)
