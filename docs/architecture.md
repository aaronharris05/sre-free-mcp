# Architecture

sre-free-mcp is a **per-customer-install** SRE toolkit. Each install runs inside one GCP project (the same project whose state it audits) and ships as a single container image used by both a long-running MCP server and a short-lived runner job.

This doc is the system-design tour. Read it once to understand how the pieces fit; the per-bot docs in [bots/](bots/) are the reference manual for each individual audit.

## System diagram

```
┌────────────────────────────────────────────────────────────────────────────────┐
│                          Customer GCP Project                                  │
│                                                                                │
│   ┌────────────────┐  HTTPS/SSE   ┌─────────────────────────┐                  │
│   │  MCP client    │───────IAM───▶│  Cloud Run service       │                  │
│   │ (Claude / IDE) │              │  sre-mcp-server          │                  │
│   └────────────────┘              │  FastMCP, port 8080      │                  │
│                                   │  9 tools                 │                  │
│                                   └─────────┬───────────────┘                   │
│                                             │                                   │
│   ┌────────────────┐  --task=X args         │                                   │
│   │ Cloud Scheduler│───────────────────┐    │                                   │
│   │ × 11 crons     │                   ▼    ▼                                   │
│   └────────────────┘             ┌─────────────────────────┐                    │
│                                  │  Cloud Run job          │                    │
│                                  │  sre-runner             │                    │
│                                  │  20 tasks dispatchable  │                    │
│                                  └─────────┬───────────────┘                    │
│                                            │                                    │
│           ┌────────────────────────────────┼────────────────────────┐           │
│           │ reads/writes via google-cloud-bigquery (lazy import)    │           │
│           ▼                                                                     │
│   ┌──────────────────────────────────────────────────────────────────────┐      │
│   │  BigQuery dataset                                                    │      │
│   │  governance / sre_governance / <your name>                           │      │
│   │                                                                      │      │
│   │   workflows · events · gap_reports · approval_queue · incidents      │      │
│   │   pii_findings · cost_daily · cloud_monitoring_alerts                │      │
│   │   pipeline_health_v1   (view)                                        │      │
│   └──────────────────────────────────────────────────────────────────────┘      │
│                                                                                 │
│   ┌──────────────────┐ ┌──────────────────┐ ┌──────────────────┐                │
│   │ Cloud Run Admin  │ │ Cloud Monitoring │ │ Secret Manager   │                │
│   │ API              │ │ API              │ │                  │                │
│   │ retry triggers   │ │ alert sync+pull  │ │ SMTP, LLM keys   │                │
│   └──────────────────┘ └──────────────────┘ └──────────────────┘                │
└─────────────────────────────────────────────────────────────────────────────────┘
                                          │
              OUTBOUND (optional)         │
                                          ▼
                              ┌─────────────────────┐
                              │ SMTP server         │ ← email findings
                              │ LLM provider API    │ ← RCA narratives
                              │ GitHub REST API     │ ← SCM audit (read)
                              │ Cloud DLP API       │ ← PII inspection
                              └─────────────────────┘
```

## Two runtime shapes

Both shapes run the same container image. The difference is the entrypoint.

### `sre-mcp-server` (Cloud Run service)

Long-running HTTP/SSE server built on [FastMCP](https://github.com/jlowin/fastmcp). Cold-starts on the first request and stays warm via min-instance scaling. Exposes nine MCP tools (see below). Authenticated via Cloud Run's IAM (`run.invoker` on the service; the Terraform module leaves `service_invokers = []` so it's private by default).

### `sre-runner` (Cloud Run job)

Short-lived batch job triggered by Cloud Scheduler. Each scheduler cron passes a `--task=<name>` arg via `containerOverrides` so a single job binary runs all 20 named tasks. Defaults to `--task=retry_tick` for manual invocations.

Both share the same `runner/tasks.py` dispatch — the MCP server's `run_task` tool literally calls the same code paths the scheduler does. On-demand and scheduled invocations behave identically.

## The bot pattern

Every audit bot is structured the same way:

```
core/<bot_name>/
├── __init__.py          # public surface (sweep, evaluate, …)
├── checks.py            # PURE-LOGIC rules — no I/O. Takes a snapshot
│                        # dataclass, returns list[Finding].
├── audit.py             # I/O-orchestrating sweep(). Pulls state from
│                        # BigQuery / GCP APIs / filesystem, calls
│                        # checks.evaluate, writes findings via
│                        # core.findings.write_findings.
└── (targets.py)         # Pydantic schema for the bot's config YAML
                         # if applicable.
```

The strict split means tests stay light: `checks.py` tests pass plain Python dataclasses through `evaluate()` and assert on the returned findings. `audit.py` tests stub the BigQuery / GCP client via monkeypatch on the module-level loader functions. No bot's test suite needs live GCP.

The audit-bot template makes adding a new bot trivial — see [extending.md](extending.md).

## Data flow

### Write path — an audit fires

1. **Cloud Scheduler** (every cron) invokes the Cloud Run job with `--task=anomaly_sweep` (or one of the other 19 names).
2. **Runner** loads `config/install.yaml` (or its `.example.yaml` fallback) and overrides project / region / dataset from env vars set by Terraform.
3. **Bot sweep** runs: reads its target list from config + state from BigQuery / GCP / filesystem; applies `evaluate()`; bulk-inserts findings into `gap_reports` via `core/findings.py::write_findings`.
4. **Idempotency** — finding IDs are deterministic UUID5 over `(scope, scope_id, gap_kind, as_of, generated_at)`. Re-running the same sweep within the same minute produces the same IDs.
5. **Logs** — each tick writes one structured-JSON line to stdout that Cloud Logging picks up automatically.

### Read path — operator asks for state

1. **MCP client** (Claude Desktop, Cursor, custom AI agent) opens an SSE connection to the Cloud Run service URL.
2. **Auth** — bearer-token Identity Token from `gcloud auth print-identity-token`. Cloud Run's IAM rejects anything else (unless you set `service_invokers = ["allUsers"]`, which we don't recommend).
3. **Tool call** — the client invokes `pipeline_health()`, `recent_findings(scope="cost", limit=20)`, `lookup_workflow("name")`, etc. The server query BigQuery and returns JSON.
4. **No state in the server** — all state lives in BigQuery. The Cloud Run service can scale to zero and back without losing anything.

### Reactive path — findings drive bots

Three reactive bots consume what the audits produce:

- **`incidents`** — every `incidents_tick` opens incidents for workflows with unresolved critical findings, closes ones that have been quiet for the configured window. Writes to `incidents` table.
- **`rca`** — every `rca_tick` reads pending `approval_queue` items with `action_kind='retry_exhausted'`, gathers recent context, asks the configured `LLMProvider` for a narrative, writes back to the queue row's `narrative` column.
- **`rollup`** — weekly cross-bot summary. Aggregates open findings + incidents + pending approvals, optionally promotes high-severity findings to `approval_queue`, emails the result.

## BigQuery schema

Eight base tables + one view. Every bot writes to a subset; the schema is bundled as nine SQL files in `src/sre_free_mcp/core/ddl/` applied in lexical order by `sre-runner --task=install_ddl`.

| Table | Purpose | Writer(s) |
|---|---|---|
| `workflows` | Registry — one row per workflow you want monitored. Carries cron, idempotent flag, owner team, business purpose, freshness/cost SLAs. | Customer (via `register_workflow` MCP tool or direct SQL) |
| `events` | Append-only audit log. Every bot emits events here. The retry circuit breaker counts rows in this table. | retry, run-time monitoring |
| `gap_reports` | Every finding from every audit. The unified surface for "what's wrong." | all audit bots |
| `approval_queue` | Human-in-the-loop items: retry exhaustions, escalated findings, RCA narratives awaiting review. | retry (exhausted), rollup (promotion), rca (writes narrative) |
| `incidents` | First-class incident lifecycle. Groups findings + events under one workflow across the open→close window. | incidents |
| `pii_findings` | Cloud DLP-specific findings. Separate table because retention semantics differ. | pii |
| `cost_daily` | Pre-aggregated daily spend with rolling 28-day baseline + z-score. Populated by a customer-side ETL (Terraform-installable scheduled query). | (external ETL) |
| `cloud_monitoring_alerts` | Cache of currently-firing GCP Cloud Monitoring incidents. | cloud_monitoring |
| `pipeline_health_v1` | **View.** Per-workflow red/yellow/green derived from workflows + recent events + open `gap_reports` filtered to scope='workflow'. | (computed) |

Full DDL: [`src/sre_free_mcp/core/ddl/`](../src/sre_free_mcp/core/ddl/).

### Idempotent rerun

Every DDL statement is `CREATE TABLE IF NOT EXISTS` or `CREATE OR REPLACE VIEW`. Re-running `install_ddl` against an already-installed dataset is a no-op for tables, a refresh for the view.

### Dataset rename

The dataset name is configurable via `governance_dataset` in `install.yaml` (or `SRE_GOVERNANCE_DATASET` env var, set by Terraform). Customers with an existing `governance` dataset from other tooling can install alongside by setting it to `sre_governance` or anything non-conflicting.

## Configuration

Five YAML files. Each is optional except `install.yaml`; missing files fall back to bundled `*.example.yaml`. Cross-validators catch typos at startup (e.g., an audit target routing to an undeclared recipient group fails fast rather than at email-send time).

| File | Required? | Purpose |
|---|---|---|
| `install.yaml` | Yes (or `.example.yaml`) | Project, region, dataset, email + LLM provider |
| `retry_policies.yaml` | No | Per-workflow retry overrides |
| `recipients.yaml` | No | Team groups → email lists |
| `anomaly_targets.yaml` | No | Tables + metric columns to scan |
| `freshness_targets.yaml` | No | Tables with refresh SLAs |
| `alert_policies.yaml` | No | Declarative GCP Cloud Monitoring policies |
| `pii_targets.yaml` | No | Tables to inspect with Cloud DLP |

Full reference: [configuration.md](configuration.md).

## Pluggable providers

Two abstract interfaces let customers swap implementations without forking:

### `EmailSender` (`src/sre_free_mcp/email_sender.py`)

```python
class EmailSender(abc.ABC):
    @abc.abstractmethod
    def send(self, message: EmailMessage) -> None: ...
```

Built-in: `NullEmailSender` (logs and records, used in tests), `SmtpEmailSender` (stdlib `smtplib`, works with Gmail / SendGrid / SES SMTP). Add your own by subclassing.

### `LLMProvider` (`src/sre_free_mcp/llm.py`)

```python
class LLMProvider(abc.ABC):
    @abc.abstractmethod
    def generate(self, prompt: str, *, max_tokens: int, temperature: float) -> str: ...
```

Built-in: `NullLLMProvider` (deterministic sentinel; bots that detect it fall back to non-LLM narratives), `GeminiLLMProvider` (Google `google-genai`). Anthropic / OpenAI / local models — subclass and inject.

## Security model

### Service account

A single SA (`sre-free-mcp@<project>.iam.gserviceaccount.com`) runs both the service and the job. Created by Terraform; granted these project-level roles:

| Role | Why |
|---|---|
| `roles/bigquery.dataEditor` | Write findings, events, incidents |
| `roles/bigquery.jobUser` | Run queries |
| `roles/run.invoker` | Retry orchestrator triggers Cloud Run jobs |
| `roles/run.viewer` | job_uptime audit lists jobs + executions |
| `roles/cloudscheduler.viewer` | job_uptime audit reads pause state |
| `roles/monitoring.alertPolicyEditor` | cloud_monitoring sync |
| `roles/monitoring.viewer` | cloud_monitoring sweep reads alerts |
| `roles/logging.logWriter` | Container logs (Cloud Run default) |

Per-secret accessor bindings (`roles/secretmanager.secretAccessor`) are granted only when the customer actually configured `smtp_password_secret` / `llm_api_key_secret`.

### MCP server auth

Private by default — `service_invokers = []` in the Terraform module. To allow specific identities, set `service_invokers = ["user:alice@example.com", "serviceAccount:bot@p.iam.gserviceaccount.com"]`. Setting `["allUsers"]` makes it public, which we **do not recommend**.

### SQL injection safety

Every BigQuery query that takes user-controlled input uses query parameters. The two exceptions — `INFORMATION_SCHEMA` paths in the freshness audit and the `governance` dataset interpolation in the DDL installer — both pass through a strict `[A-Za-z_][A-Za-z0-9_]*` identifier validator before string concatenation.

## Extension model

Adding a new bot doesn't require modifying core code. The pattern:

1. `src/sre_free_mcp/core/<bot>/__init__.py`, `checks.py`, `audit.py`, optional `targets.py` for config schema.
2. Register a task in `src/sre_free_mcp/runner/tasks.py` — a thin wrapper that calls your `sweep()`.
3. Add an entry to `enabled_tasks` in the Terraform module's `variables.tf` with the cron you want.
4. Tests follow the established `tests/core/<bot>/` layout.

Full walkthrough: [extending.md](extending.md).

## Per-customer install model

The repo is one repo, but it's designed to be installed N times (once per customer GCP project) rather than run as a multi-tenant SaaS. Reasons:

- **No cross-project IAM headaches.** All bots run inside the customer's project, with that project's SA, against that project's data. No service-account impersonation across orgs.
- **Easier compliance story.** PII findings stay in the customer's BQ; SCC findings stay in their org; LLM keys stay in their Secret Manager.
- **Simpler upgrade path.** A customer pulls a new image tag and re-applies Terraform. No coordination with a central operator.
- **Same code-path for all installs.** Whether you're running it for your own monorepo or a customer's, the bits running are identical.

Trade-off: if you want one dashboard across multiple projects, you have to aggregate at query time (cross-project BigQuery scheduled queries, federated views, or your own rollup script). The unified MCP surface is local to one install.

## See also

- [quickstart.md](quickstart.md) — 10-minute install walkthrough
- [configuration.md](configuration.md) — every YAML field documented
- [mcp-clients.md](mcp-clients.md) — Claude Desktop / Cursor / `mcp` CLI setup
- [extending.md](extending.md) — write your own bot
- [bots/](bots/) — per-bot reference for all 17 bots
