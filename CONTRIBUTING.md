# Contributing

Bug reports, feature requests, and pull requests welcome.

## Reporting bugs

Open an issue. Useful template:

- What you ran (the gcloud / terraform / pytest command)
- What you expected
- What actually happened (logs / error text — full traceback if it's a Python crash)
- Versions: Python (`python --version`), Terraform (`terraform version`), gcloud (`gcloud version`)
- If the issue is in a deployed install: the relevant `gcloud logging read` excerpt

## Proposing a feature

Open an issue describing the use case before writing code. Most features land more smoothly when the design conversation happens up front. Tag with `enhancement`.

## Dev setup

```bash
git clone https://github.com/aaronharris05/sre-free-mcp.git
cd sre-free-mcp

python -m venv .venv
source .venv/bin/activate  # or .venv\Scripts\activate on Windows

pip install -e ".[dev]"
```

Confirm the suite passes:

```bash
pytest
```

Should see `473 passed in ~5s` (or whatever the current count is at the time you read this).

## What goes where

```
src/sre_free_mcp/
├── core/                 # Per-bot modules + shared models / tables / findings
│   ├── retry/
│   ├── anomaly/
│   └── ...
├── config.py             # YAML loader + pydantic schemas
├── llm.py                # LLMProvider ABC + built-in subclasses
├── email_sender.py       # EmailSender ABC + SMTP / Null subclasses
├── secrets.py            # Secret Manager helper
├── runner/               # sre-runner CLI (the Cloud Run job entrypoint)
│   ├── __main__.py
│   └── tasks.py          # Task registry — every Cloud Scheduler cron lands here
└── mcp_server/           # sre-mcp-server (the Cloud Run service entrypoint)
    └── server.py         # FastMCP-based MCP server with 9 tools

tests/                    # Mirrors src/ layout
config/                   # *.example.yaml — customer configs
infra/terraform/          # Reusable module + examples
docker/                   # Dockerfile + entrypoint
docs/                     # Architecture, quickstart, per-bot docs
```

## Conventions

### Adding a new bot

Read [docs/extending.md](docs/extending.md) — full walkthrough with a worked example. TL;DR: `core/<bot>/checks.py` (pure logic) + `audit.py` (I/O) + a one-line entry in `runner/tasks.py`.

### Test discipline

- **Every PR that adds/modifies Python code must include unit tests.** Test files mirror the src layout (`src/foo/bar.py` → `tests/foo/test_bar.py`).
- Tests should NOT need GCP credentials. Stub `google.cloud.*` clients via `monkeypatch.setattr(my_module, "list_open_incidents", lambda p: [...])`. The codebase is structured to keep this easy — every I/O call lives in a module-level function that's monkeypatchable.
- Tests should NOT need a network. If your bot calls an HTTP API, lazy-import the HTTP client inside the function and stub the call.
- Test names: `test_<rule_name>_<expected_outcome>`. E.g., `test_unrotated_secret_fires_medium`, `test_breaker_closed_below_threshold`.

### Schema changes

If you add a column to a `governance.*` table:

1. Edit the relevant `.sql` file in `src/sre_free_mcp/core/ddl/`.
2. Document the column in the file header comment.
3. Run `terraform -chdir=infra/terraform/examples/minimal validate` to catch syntax issues.
4. In a follow-up commit, add migration notes to CHANGELOG explaining how existing installs upgrade (typically `ALTER TABLE ... ADD COLUMN` since `CREATE TABLE IF NOT EXISTS` won't update existing tables).

### Commits

Conventional-commit-ish style:

- `feat: <module>: <one-line description>`
- `fix: <module>: <one-line description>`
- `chore:`, `docs:`, `test:`, `refactor:`, `ci:` for non-functional changes

Body explains the *why* (one or two short paragraphs), not the *what* (the diff is the what).

End commits with a Co-Authored-By line if multiple people contributed.

### PRs

- One logical change per PR. Prefer smaller PRs that land quickly over big ones that block on review.
- The PR description should mention: what changed, why, how you tested it. A bullet list of files in the description isn't necessary — the file tree shows that.
- All CI checks must be green before merge.
- Don't merge your own PRs without at least one approval (unless you're the maintainer and the change is mechanical).

### Style

- Python: ruff handles formatting + lint. Run `ruff check src tests --fix` before committing.
- Markdown: 100-char lines preferred but not enforced; tables can run wider.
- Terraform: `terraform fmt` before committing. CI checks this.

### Comments

- Default to **no comments**. Well-named identifiers explain themselves.
- Comment only when the *why* is non-obvious: hidden constraints, subtle invariants, workarounds for specific upstream bugs.
- Never explain *what* the code does (the diff and the type signatures already do).
- Never reference the current task / fix ("used by X", "added for Y") — that belongs in the PR description and rots as the codebase evolves.

### Docs

If your change affects user-visible behavior (new config field, new bot, schedule change, schema change), update:

- The relevant page in `docs/`
- `README.md` if the change is prominent (new bot, new top-level concept)
- `CHANGELOG.md` under the next unreleased version

## Pre-commit hooks (optional)

```bash
pip install pre-commit
pre-commit install
```

Runs ruff + terraform fmt + a trailing-whitespace cleaner on every `git commit`. Skip with `git commit --no-verify` if you really need to (CI will catch you anyway).

## Releasing

(Maintainer notes — most contributors don't need this.)

1. Update version in `pyproject.toml`.
2. Update `CHANGELOG.md` — move "Unreleased" to the new version, add the date.
3. Commit + tag: `git tag v0.X.Y && git push origin v0.X.Y`.
4. GitHub Release with the changelog excerpt as the body.

## License

By contributing, you agree your contributions are licensed under [Apache 2.0](LICENSE).
