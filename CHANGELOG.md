# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Per-bot documentation under `docs/bots/` covering all 17 bots.
- `docs/architecture.md` — system design tour, data flow, security model.
- `docs/quickstart.md` — 10-minute install walkthrough.
- `docs/configuration.md` — full YAML field reference.
- `docs/mcp-clients.md` — Claude Desktop, Cursor, `mcp` CLI, custom Python.
- `docs/extending.md` — walkthrough for adding a new bot.
- `.github/workflows/ci.yml` — GitHub Actions pipeline (pytest + ruff + terraform validate).
- `CONTRIBUTING.md` — dev setup, conventions, PR process.

### Changed

- README rewritten as a polished landing page with bot index, architecture diagram, quickstart, and config overview.

## [0.1.0] — 2026-05-13

First public release.

### Added

- **16 audit bots** writing to `governance.gap_reports`:
  - `anomaly` — Isolation Forest outliers on configured BigQuery numeric columns
  - `job_uptime` — missed runs, failing streaks, paused schedulers, dead registrations
  - `freshness` — tables not updated on declared cadence
  - `cost` — daily-spend z-score against rolling 28-day baseline
  - `cloud_monitoring` — GCP alert policy sync + active-incident pull
  - `data_catalog` — structural completeness of the workflow registry
  - `ai_gov` — policy compliance (business_purpose, policy_refs)
  - `dependency` — pyproject.toml / requirements.txt risk
  - `scm` — git/branch/PR hygiene via GitHub REST API
  - `test_coverage` — Cobertura XML coverage audit
  - `secret_iam` — Secret Manager rotation + project IAM hygiene
  - `security` — Security Command Center findings forwarder
  - `pii` — Cloud DLP inspection of BigQuery tables
  - `llm_safety` — drift + adversarial probes against configured LLM
  - `incidents` — open/close lifecycle grouping findings + events
  - `rollup` — weekly cross-bot summary email + promotion to approval_queue
- **Retry orchestrator** with circuit breaker, per-workflow policy registry, deterministic backoff curves.
- **RCA bot** generating LLM-narrated root-cause for exhausted retries.
- **MCP server** (`sre-mcp-server`) exposing 9 tools over HTTPS/SSE: `pipeline_health`, `recent_findings`, `list_workflows`, `lookup_workflow`, `register_workflow`, `run_task`, `list_tasks`, `open_incidents`, `pending_approvals`.
- **Runner CLI** (`sre-runner`) dispatching to 20 named tasks via `--task=X`.
- **Pluggable provider ABCs** — `EmailSender` (Null / SMTP), `LLMProvider` (Null / Gemini); subclass for Anthropic / OpenAI / local models.
- **YAML config layer** — pydantic-validated `install.yaml`, `retry_policies.yaml`, `recipients.yaml`, `anomaly_targets.yaml`, `freshness_targets.yaml`, `alert_policies.yaml`, `pii_targets.yaml`. Cross-validators catch typos at startup.
- **BigQuery schema** — 8 base tables + 1 view in the `governance` dataset (configurable name). Installed via `sre-runner --task=install_ddl`.
- **Terraform module** — `infra/terraform/modules/sre/` with a minimal example. Creates Cloud Run service + job + 11 schedulers + BQ dataset + IAM in one apply.
- **473 unit tests** covering rule logic, schema validation, sweep orchestration, config loading, MCP server tool handlers.
- **Apache 2.0 license**.

### Known limitations

- `dependency`, `scm`, `test_coverage` accept inputs via env vars rather than YAML — proper YAML schemas land in 0.2.
- `pii`, `security` require optional extras (`pip install sre-free-mcp[pii]` / `[security]`) — not bundled in the default container image.
- Multi-file Secret-Manager mount only supports `install.yaml`; other YAMLs must be baked into the image.
- The anomaly engine is IsolationForest-only — the full leaderboard (IF + LOF + OCSVM + ECOD + PCA) is on the v2 roadmap.

[Unreleased]: https://github.com/aaronharris05/sre-free-mcp/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/aaronharris05/sre-free-mcp/releases/tag/v0.1.0
