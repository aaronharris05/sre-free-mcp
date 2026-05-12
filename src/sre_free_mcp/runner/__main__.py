"""``sre-runner`` CLI — Cloud Run job entrypoint.

Triggered by Cloud Scheduler with ``--task=<name>``. Loads config,
builds the BigQuery client, dispatches to the requested task, prints
the JSON-serialized result to stdout (Cloud Run picks it up as a log
line), and exits 0.

Exit codes:
  0 — task completed successfully
  2 — unknown task name
  3 — configuration error
  10 — task raised an exception
"""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import click

from sre_free_mcp.config import apply_retry_overrides, load_config

from .tasks import TASKS, dispatch

logger = logging.getLogger(__name__)


@click.command()
@click.option(
    "--task",
    required=True,
    type=click.Choice(sorted(TASKS.keys()), case_sensitive=False),
    help="Which task to run. See TASKS in sre_free_mcp.runner.tasks.",
)
@click.option(
    "--config-dir",
    default=lambda: os.environ.get("SRE_CONFIG_DIR", "/app/config"),
    type=click.Path(file_okay=False),
    help="Directory containing install.yaml + other YAMLs. Defaults to "
    "$SRE_CONFIG_DIR or /app/config.",
)
@click.option(
    "--log-level",
    default="INFO",
    type=click.Choice(["DEBUG", "INFO", "WARNING", "ERROR"], case_sensitive=False),
)
def main(task: str, config_dir: str, log_level: str) -> None:
    """Run one sre-free-mcp task."""
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(levelname)s %(name)s %(message)s",
    )

    try:
        config = load_config(Path(config_dir))
    except FileNotFoundError as e:
        click.echo(f"config_error: {e}", err=True)
        sys.exit(3)
    except Exception as e:
        click.echo(f"config_error: {type(e).__name__}: {e}", err=True)
        sys.exit(3)

    n_overrides = apply_retry_overrides(config)
    logger.info("loaded config; %d retry overrides applied", n_overrides)

    # Lazy import keeps CLI startup fast for `--help`.
    from google.cloud import bigquery  # lazy

    bq_client = bigquery.Client(project=config.install.project_id)
    now = datetime.now(UTC)

    try:
        result = dispatch(task, config=config, bq_client=bq_client, now=now)
    except KeyError as e:
        click.echo(f"unknown_task: {e}", err=True)
        sys.exit(2)
    except Exception:
        logger.exception("task %s raised", task)
        sys.exit(10)

    payload = {
        "task": task,
        "project": config.install.project_id,
        "generated_at": now.isoformat(),
        "result": result,
    }
    click.echo(json.dumps(payload, default=str))


if __name__ == "__main__":
    main()
