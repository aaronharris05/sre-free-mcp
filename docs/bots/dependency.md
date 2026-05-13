# dependency — scan pyproject.toml / requirements.txt for risk

The dependency bot scans configured Python dependency files and flags risky declarations: unpinned packages, wildcards, pre-release pins.

**Module:** [`core/dependency/`](../../src/sre_free_mcp/core/dependency/)
**Task name:** `dependency_audit`
**Default schedule:** disabled by default — enable by setting `enabled_tasks.dependency_audit = "0 4 * * 1"` (or any cron) in the Terraform module

## How one audit works

1. Read paths from the `SRE_DEPENDENCY_SOURCES` env var (comma-separated).
2. For each path, dispatch to the right parser:
   - `pyproject.toml` → `parse_pyproject` (PEP 621 + Poetry fallback)
   - `*.txt` → `parse_requirements` (pip-style, comments + `-r` includes stripped)
3. Apply three rules per dependency.
4. Bulk-write findings to `gap_reports` with `scope='dependency'`.

## Rules produced

| gap_kind | severity | rule |
|---|---|---|
| `unpinned_dependency` | high | No version specifier at all — any future release pulled in |
| `wildcard_dependency` | medium | `*` or `>=X` without upper bound — major-version risk |
| `pre_release_pin` | low | `==X.Yalpha` / `rc` / `beta` / `dev` — not stable for production |

## Config

Via env var on the Cloud Run job:

```hcl
# In your fork of the Terraform module, set on the job:
env {
  name  = "SRE_DEPENDENCY_SOURCES"
  value = "/app/repo/pyproject.toml,/app/repo/requirements.txt"
}
```

Pre-v2, the customer is responsible for mounting their repo into the container — typically via a Cloud Build step that copies relevant files into the image, OR via a GCS bucket / volume mount.

A v2 candidate: a `dependency_sources.yaml` config that lets customers declare sources without env-var dance.

## Why CVE detection is deferred to v2

CVE lookup requires querying an external vulnerability DB (NVD, OSV, GitHub Advisory). That's a runtime network dependency and adds significant complexity. v1 catches the most-actionable risk (unpinned / wildcard / pre-release) without external lookups; CVE integration follows in v2 as a wrapper around [pip-audit](https://github.com/pypa/pip-audit) or similar.

## Tables read / written

**Reads** — local filesystem (whatever paths `SRE_DEPENDENCY_SOURCES` points at).

**Writes** — `governance.gap_reports` (scope='dependency').

## Example finding

```json
{
  "scope": "dependency",
  "scope_id": "/app/repo/pyproject.toml:django",
  "gap_kind": "unpinned_dependency",
  "severity": "high",
  "details": {
    "package": "django",
    "specifier": "",
    "source": "/app/repo/pyproject.toml",
    "group": null,
    "rule": "no version specifier — any future release may break the build or introduce vulnerabilities"
  }
}
```

## How the parsers handle environment markers

PEP 508 environment markers (`pydantic ; python_version>='3.10'`) are stripped before evaluation — we don't audit on marker conditions, only on the package + specifier.

## Common questions

**Q: Does this audit my Node / Go / Rust dependencies?**
Not in v1. The parsers handle pyproject.toml and requirements.txt only. To audit `package.json`, `go.mod`, or `Cargo.toml`, write a new parser in `core/dependency/parsers.py` and the existing rule logic in `checks.py` works as-is on the resulting `Dependency` records.

**Q: How do I exclude dev-only dependencies?**
The parser captures `group` (e.g., `dev`, `docs` for PEP 621 optional-dependencies). To skip findings for one group, filter in `runner/tasks.py::dependency_audit` after the sweep — or extend `evaluate()` to take a `skip_groups` arg.

**Q: My deps are managed by uv / pdm / hatch — will it work?**
For tools that respect PEP 621 (`[project]` table), yes. Poetry has a fallback. Pixi / Conda envs need a custom parser.

**Q: Why doesn't this run on every push, just weekly?**
You CAN run it more often — the bot is a cheap pure-Python sweep, no GCP API calls. Bump the cron to daily or hourly if you want. The weekly default is for "out of the box low noise."
