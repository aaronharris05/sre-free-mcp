# test_coverage — Cobertura XML coverage audit

The test_coverage bot parses Cobertura-format coverage reports (the standard output of pytest-cov, coverage.py, plus most Java / .NET tooling) and flags overall + per-module coverage below thresholds.

**Module:** [`core/test_coverage/`](../../src/sre_free_mcp/core/test_coverage/)
**Task name:** `test_coverage_audit`
**Default schedule:** disabled by default — enable when you've populated `SRE_COVERAGE_REPORTS`

## How one audit works

For each path in `SRE_COVERAGE_REPORTS`:

1. Parse the Cobertura XML.
2. Extract overall `line-rate` + per-class (file) `line-rate` + `lines-valid` count.
3. Apply two rules.
4. Bulk-write findings to `gap_reports` with `scope='test_coverage'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `low_overall_coverage` | high | `overall_line_rate < overall_threshold` (default 0.80) |
| `low_module_coverage` | medium | Per-file rate below `module_threshold` (default 0.60), excluding files smaller than `min_lines_for_module_check` |

## Config

Via env var:

```bash
SRE_COVERAGE_REPORTS=/app/repo/coverage.xml,/app/other/coverage.xml
```

Path resolution: each entry must point at a Cobertura XML file the container can read — mount via Cloud Build, GCS volume, or build into the image.

## Tunable knobs

`sweep()` (via the runner task) exposes:

| Arg | Default | Notes |
|---|---|---|
| `overall_threshold` | 0.80 | Fraction of lines covered globally. 0.80 = 80%. |
| `module_threshold` | 0.60 | Per-file threshold. |
| `min_lines_for_module_check` | 20 | Skip tiny files where one missed line tanks the percentage. |

## Tables read / written

**Reads** — local Cobertura XML files.

**Writes** — `governance.gap_reports` (scope='test_coverage').

## Example finding

```json
{
  "scope": "test_coverage",
  "scope_id": "/app/repo/coverage.xml:app/billing.py",
  "gap_kind": "low_module_coverage",
  "severity": "medium",
  "details": {
    "source": "/app/repo/coverage.xml",
    "filename": "app/billing.py",
    "line_rate": 0.42,
    "lines_valid": 137,
    "threshold": 0.60
  }
}
```

## Why Cobertura?

Cobertura's XML schema is the most-portable coverage format. pytest-cov outputs it (`--cov-report=xml`), coverage.py outputs it (`coverage xml`), Java/.NET tooling produces it. Sticking to Cobertura means the same parser works across stacks.

For tools that emit a different format (Istanbul JSON for JS, llvm-cov for Rust), convert to Cobertura first — most tools have a converter — or add a new parser to `core/test_coverage/parser.py`.

## Common questions

**Q: How do I exclude generated files from the per-module check?**
The parser skips classes with `lines-valid=0`. Beyond that, exclude in your test runner's config (`.coveragerc`'s `[run] omit`, jest's `coveragePathIgnorePatterns`). Files that don't appear in the XML can't be flagged.

**Q: Does this track coverage trends over time?**
Not in v1. Each sweep is a point-in-time snapshot. `regressed_coverage` (file dropped vs prior run) is a v2 candidate — would need a `coverage_history` table to compare against.

**Q: What if my test suite produces multiple coverage.xml files (per-package)?**
List them all in `SRE_COVERAGE_REPORTS`. Each gets its own findings; the overall threshold applies to each report independently. To get a true "overall" across packages, merge with `coverage combine` before the audit.

**Q: I want different thresholds for different parts of the codebase.**
v1: doesn't support that out of the box. Easiest path is two coverage.xml files (one per part) with different thresholds passed via separate task invocations. v2: per-report tunable thresholds.
