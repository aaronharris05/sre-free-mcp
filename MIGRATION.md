# Migration tracker — lifting SRE from agentic-energy

Internal planning doc. Tracks what we still need to move from `agentic-energy/agents/sre/` into this repo, and the cutover gate.

This file should be **deleted** before the first public release. Keep it during the lift so we don't lose track.

## Source code to lift

Source files in `c:\Users\aaron\Documents\GitHub\agentic-energy\agents\sre\` → target paths here.

| Status | Source | Target |
|---|---|---|
| [x] | `agents/sre/retry.py`                  | `src/sre_free_mcp/core/retry/orchestrator.py` — `tick()` returns `TickResult`; project/region required args; `GovernanceTables` threads dataset rename through every table reference; self-registration dropped |
| [x] | `agents/sre/_retry_policy.py`          | `src/sre_free_mcp/core/retry/policy.py` — overrides removed; `register_override()` added for config loader |
| [x] | `agents/sre/_circuit_breaker.py`       | `src/sre_free_mcp/core/retry/breaker.py` — `events_table` param added for dataset rename |
| [x] | `agents/sre/data_anomaly.py`           | `src/sre_free_mcp/core/anomaly/detector.py` — `sweep()` returns `SweepResult`; email step removed (next slice); dry-run flag added; self-registration dropped |
| [x] | `agents/sre/_anomaly_targets.py`       | `src/sre_free_mcp/core/anomaly/targets.py` — pydantic frozen model + YAML loader; hardcoded targets removed; cross-validator ensures owner_team exists in recipients |
| [x] | `agents/sre/_anomaly_router.py`        | `src/sre_free_mcp/core/anomaly/router.py` — `owner_for(targets, ...)` takes target list as arg; module-level state removed; fallback team configurable |
| [ ] | `agents/sre/_anomaly_email.py`         | `src/sre_free_mcp/core/anomaly/email.py` |
| [ ] | (deferred v2) `agents/sre/tools/*`     | `src/sre_free_mcp/mcp_server/tools/health.py` |

## Dependencies to vendor / refactor

Code in `agents/_base/`, `agents/data_governance/`, `mcp_server/`, `tools/email/` that the SRE files import:

| Status | Source | Disposition |
|---|---|---|
| [ ] | `agents/_base/governance_hooks.py`           | Strip to minimal `workflows` table self-registration; drop Dataplex catalog integration |
| [x] | `agents/_base/_email_sender.py`              | `src/sre_free_mcp/email_sender.py` — `EmailSender` ABC + `NullEmailSender` + `SmtpEmailSender` + `default_sender()` factory (resolves SMTP password from Secret Manager) |
| [x] | `mcp_server/_llm.py`                         | `src/sre_free_mcp/llm.py` — `LLMProvider` ABC + `NullLLMProvider` + `GeminiLLMProvider` + `default_provider()` factory; `NULL_LLM_SENTINEL` for fallback detection |
| [x] | `agents/data_governance/_registry_checks.Finding` | `src/sre_free_mcp/core/models.py` — frozen dataclass, scope/scope_id/gap_kind/severity/details |
| [x] | `ds_agent.anomaly_detection.auto_anomaly_select` | `src/sre_free_mcp/core/anomaly/engine.py` — IsolationForest only for v1; `score()` + `z_equivalent()` |
| [x] | `agents/data_governance/_audit_narrative.py` | Folded directly into `src/sre_free_mcp/core/anomaly/email.py` — `_generate_narrative` with deterministic fallback when LLM is null or raises |
| [x] | `tools/email/recipients.yaml` loader         | `src/sre_free_mcp/config.py::RecipientsConfig.lookup()` |
| (done above) | `ds_agent.anomaly_detection.auto_anomaly_select` | — see core/anomaly/engine.py |

## Tests to lift

All under `c:\Users\aaron\Documents\GitHub\agentic-energy\tests\agents\sre\`:

- [x] `test_retry_policy.py` → `tests/core/retry/test_policy.py` (agentic-energy override-specific tests dropped; replaced with registration-mechanism tests)
- [x] `test_retry_smoke.py` → `tests/core/retry/test_orchestrator.py` (12 tests, including 4 new ones: mixed outcomes, no-candidates, per-workflow exception isolation, dataset-rename flow-through)
- [x] `test_circuit_breaker.py` → `tests/core/retry/test_breaker.py` (+ test for `events_table` rename support)
- [x] `test_anomaly_targets.py` → `tests/core/anomaly/test_targets.py` (schema + duplicate detection + active filter + lookup)
- [x] `test_anomaly_router.py` → `tests/core/anomaly/test_router.py` (routing + customizable fallback)
- [x] `test_anomaly_email_smoke.py` → `tests/core/anomaly/test_email.py` (14 tests: subject formats, fallback narrative paths, LLM exception fallback, HTML escaping, custom gap_reports table)
- [ ] (deferred v2) `test_agent.py`, `tools/test_*`

Scrub fixtures so no agentic-energy-specific table names (`raw.pjm_lmp_da`, `raw.noaa_weather_hist_hourly`, `curated.system_load_actuals`) appear — replace with `example_dataset.example_table`.

## Terraform to write fresh

The original Terraform in `agentic-energy/infra/terraform/environments/dev/main.tf` is wired into agentic-energy's modules. Don't copy — write a new reusable module:

- [ ] `infra/terraform/modules/sre/main.tf` — service + job + scheduler + BQ + IAM
- [ ] `infra/terraform/modules/sre/variables.tf` — project_id, region, schedules, image, configs
- [ ] `infra/terraform/modules/sre/outputs.tf` — MCP server URL, job names
- [ ] `infra/terraform/examples/minimal/main.tf` — wire the module to a fresh project

## DDL to ship

Will be applied by Terraform on install. Schemas should match what agentic-energy uses today so the cutover is data-compatible.

- [ ] `governance.gap_reports`
- [ ] `governance.events`
- [ ] `governance.approval_queue`
- [ ] `governance.workflows` (with `idempotent` column)
- [ ] `governance.pipeline_health_v1` (view over `workflows` + recent `events`)

## MCP tools to expose

- [ ] `scan_anomalies(targets: list[str] | None)` — runs the anomaly sweep against configured (or specified) targets
- [ ] `retry_workflow(workflow_name: str, force: bool = False)` — schedules one retry, honoring breaker unless force
- [ ] `circuit_breaker_state(workflow_name: str)` — returns closed | open | half_open
- [ ] `pipeline_health()` — returns the current red/yellow/green counts
- [ ] `recent_findings(scope: str = 'data', limit: int = 50)` — read-only summary

## Gate before deleting from agentic-energy

The agentic-energy `agents/sre/` tree does NOT get deleted until ALL of these pass:

1. [ ] `pytest` in sre-free-mcp green on all lifted tests
2. [ ] `pytest` integration suite green (MCP server up, scheduler triggers runner, runner calls tool, finding lands in BQ, email sent — on synthetic data)
3. [ ] Sre-free-mcp installed into `agentic-energy-dev` GCP project (NOT the `sre-free` project) and one real anomaly sweep produces findings matching what `agents/sre/data_anomaly.py` would produce today
4. [ ] One real retry tick processes a known red workflow in agentic-energy-dev without disturbing in-flight runs
5. [ ] Aaron signs off

Then, in one PR on agentic-energy:
- Delete `agents/sre/`
- Delete `tests/agents/sre/`
- Replace `sre_retry_orchestrator_job` + `sre_data_anomaly_job` Terraform modules with a single `sre-free-mcp` module invocation
- Update backend chat tools that referenced SRE imports
