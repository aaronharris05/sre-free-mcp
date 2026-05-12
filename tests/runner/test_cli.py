"""Tests for the sre-runner CLI."""

from __future__ import annotations

from pathlib import Path

from click.testing import CliRunner

from sre_free_mcp.runner.__main__ import main


def _write_config(tmp_path: Path) -> Path:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "install.yaml").write_text(
        """
project_id: test-proj
email:
  from_address: sre@example.com
  smtp_host: smtp.example.com
  smtp_username: sre
  smtp_password_secret: ""
""",
        encoding="utf-8",
    )
    return cfg_dir


def test_help_lists_every_task(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    # A few task names should appear in the help text.
    assert "anomaly_sweep" in result.output
    assert "retry_tick" in result.output
    assert "rollup" in result.output


def test_cli_unknown_task_exits_with_click_error(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(main, ["--task", "not_a_task", "--config-dir", str(tmp_path)])
    # click's Choice validator rejects before our code runs → exit code 2
    assert result.exit_code == 2


def test_cli_missing_config_dir_returns_3(tmp_path: Path):
    runner = CliRunner()
    result = runner.invoke(
        main,
        [
            "--task",
            "anomaly_sweep",
            "--config-dir",
            str(tmp_path / "does-not-exist"),
        ],
    )
    assert result.exit_code == 3
    assert "config_error" in result.output


def test_cli_missing_install_yaml_returns_3(tmp_path: Path):
    runner = CliRunner()
    cfg_dir = tmp_path / "empty_cfg"
    cfg_dir.mkdir()
    result = runner.invoke(
        main, ["--task", "anomaly_sweep", "--config-dir", str(cfg_dir)]
    )
    assert result.exit_code == 3
