# HANDOFF — Open Ownership Items

> **⚠ Read this first if you are picking up SRE / ops automation work that came in from sibling repos.**
>
> This file mirrors the structure of the upstream
> [`Qorik-Labs/HANDOFF.md`](../Qorik-Labs/HANDOFF.md) — each item is a
> piece of work that needs an owner but is **not** part of the current
> active plan in this repo. The original spec is preserved verbatim
> below so the next implementer doesn't have to chase the history.

---

## 🚩 OWNERSHIP ITEM #1 — L1 / L2 support bots

**Status:** Not started. Moved here from
[`Qorik-Labs/HANDOFF.md#4`](https://github.com/aaronharris05/Qorik-Labs/blob/main/HANDOFF.md#-ownership-item-4--l1--l2-support-bots)
on 2026-05-14 because L1/L2 ops automation is an SRE concern, not a
statistics-library concern. Qorik-Labs' HANDOFF entry is now a
forwarding pointer to this file.

**Owner:** *(unassigned — handoff)*

### What

Two tiers of automated operator response, scoped to cover **any**
repo the team operates (Qorik-Labs, agentic-energy, downstream caller
repos) — not just SRE-free's own surface.

**L1 — mechanical fixes (run on CI / GitHub Actions / local daemon):**

- Re-run failed test jobs (flaky-test detection before escalation).
- Toggle feature flags (turn a module off when its dependency is
  broken upstream; turn it back on when fixed).
- Restart a stuck background task (demo suite, long
  `auto_*_select` runs, scheduled retrains).
- Auto-open an issue with the failing log + environment capture.
- Auto-close issues whose referenced tests are now passing.

**L2 — triage + routing:**

- Read an incoming issue / failure report, classify it (`statistical`,
  `plotting`, `data-pipeline`, `agent-layer`, `ops`).
- Route to the correct owner based on `CODEOWNERS` + the upstream
  repo's `HANDOFF.md` (e.g., anything mentioning `problem_advisor` or
  `agent/` → Qorik-Labs HANDOFF #1; anything mentioning `plot_` →
  plotting-sweep author; etc.).
- Summarise the issue into a standard bug-report template before
  handoff.
- Escalate to a human only when confidence on the classifier drops
  below threshold.

### Why this lives in `sre-free-mcp` (not in Qorik-Labs)

- This is ops/tooling work, orthogonal to the statistics library.
  Qorik-Labs' `ds_agent/` package is application logic; mixing in
  CI / Slack / incident tooling pollutes the surface and adds
  dependencies (GitHub API clients, Slack SDK, etc.) that
  Qorik-Labs consumers shouldn't have to install.
- The sre-free-mcp repo already deploys to its own GCP project and
  has the right shape for an ops-side daemon / scheduled-job
  surface.
- Multi-repo scope: the L2 router needs to read multiple repos'
  CODEOWNERS / HANDOFF files, which is naturally an external
  orchestration concern, not internal to any one of them.

### Open questions for the implementer

1. **Host shape.**
   - GitHub Actions reusable workflow (`workflow_call`) — cleanest
     for L1 mechanical actions; lives where the events fire.
   - Self-hosted daemon (Cloud Run job with Pub/Sub trigger) — needed
     once the L2 classifier wants persistent state (e.g. flaky-test
     historical baseline).
   - Lean: **GitHub Actions for L1, Cloud Run for L2.** Both run
     under the same `sre-free-mcp` project's service account.

2. **L2 classifier model.**
   - Claude Haiku 4.5 with a system prompt + few-shot routing
     examples — cheap, fast, doesn't need training.
   - A small fine-tuned model — only worth it if the prompt-based
     approach hits >5% mis-routing in production.
   - Lean: **Haiku-with-prompt first**, fine-tune only if measured
     accuracy is insufficient. Iterate on the prompt + few-shot
     examples; treat the prompt as versioned configuration.

3. **Permissions.**
   - L1 bot touching `main` vs branches only.
   - Lean: **branches only**. L1 actions like "re-run flaky test" or
     "auto-close issue when its test passes" don't need write
     access to `main` — they manipulate CI state and issue state.
     The only `main`-touching action would be auto-merging a green
     PR, and that should require explicit human approval upstream.

4. **Cross-repo auth.**
   - GitHub App vs PAT. A GitHub App is the right shape (per-repo
     install, scoped permissions, no human credentials in CI). The
     L2 daemon authenticates as the App for issue/PR comments.

5. **Telemetry + cost budget.**
   - Every L2 LLM call goes to a structured log with input tokens /
     output tokens / latency / classification confidence. Build a
     daily cost cap with a kill switch.
   - Per-repo budget so an out-of-control issue spam from one repo
     can't burn down the whole tier.

### Suggested structure when this lands

```
sre-free-mcp/
├── support_bots/
│   ├── __init__.py
│   ├── l1/                       # mechanical fixes
│   │   ├── flaky_test_retry.py
│   │   ├── stuck_job_restart.py
│   │   ├── issue_auto_open.py
│   │   └── issue_auto_close.py
│   ├── l2/                       # triage + routing
│   │   ├── classifier.py         # Haiku-prompted issue classifier
│   │   ├── router.py             # reads CODEOWNERS + HANDOFF.md across repos
│   │   ├── summarizer.py         # condenses an issue to the standard template
│   │   └── prompts/
│   │       ├── classifier_system.txt
│   │       └── classifier_examples.json
│   └── adapters/
│       ├── github_app.py         # GitHub App auth + REST helpers
│       └── handoff_reader.py     # parses sibling HANDOFF.md files for routing
├── infra/
│   └── support_bots/             # terraform for the Cloud Run job
└── .github/workflows/
    ├── l1-flaky-retry.yml        # workflow_call for caller repos to use
    └── l1-auto-close.yml
```

### Where it integrates upstream

- Qorik-Labs caller workflows can `uses:
  aaronharris0/sre-free-mcp/.github/workflows/l1-flaky-retry.yml@main`
  to get flaky-test retry for free.
- The L2 classifier reads
  `https://raw.githubusercontent.com/<owner>/<repo>/main/HANDOFF.md`
  on every routing decision so changes upstream propagate without a
  config rebuild.

### Acceptance criteria (when this is built)

- A flaky test in Qorik-Labs that passes on retry results in no
  human notification; the L1 workflow records the retry in the job
  log and the original PR stays green.
- A genuinely-failing test results in an issue auto-opened against
  the right CODEOWNERS section.
- An issue mentioning `problem_advisor` gets a comment within 60s
  pointing at Qorik-Labs HANDOFF #1.
- LLM cost per routing decision tracked in telemetry; daily cap
  enforced.
- All bot actions are dry-run-able via a `--dry-run` flag and a
  matching `support_bots.dry_run=true` config.

---

## How to add a new handoff item

1. Append a new `## 🚩 OWNERSHIP ITEM #N — <title>` section here.
2. Keep the format consistent with this file so a future LLM picking
   up the work can parse it.
3. If the work was forwarded from another repo, note the origin URL
   so the original tracking issue can be retired with a pointer.
