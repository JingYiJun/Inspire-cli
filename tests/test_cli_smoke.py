from click.testing import CliRunner
import pytest

from inspire.cli.main import main as cli_main


def test_cli_help_includes_top_level_groups() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["--help"])
    assert result.exit_code == 0
    assert "job" in result.output
    assert "hpc" in result.output
    assert "notebook" in result.output
    assert "image" in result.output
    assert "resources" in result.output
    assert "tunnel" in result.output


def test_completion_command_does_not_exist() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["completion"])
    assert result.exit_code != 0
    assert "No such command" in result.output


def test_click_zsh_source_exposes_standard_completion_script() -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli_main,
        [],
        env={"_INSPIRE_COMPLETE": "zsh_source"},
        prog_name="inspire",
    )
    assert result.exit_code == 0
    assert "#compdef inspire" in result.output
    assert "compdef _inspire_completion inspire" in result.output


def test_job_help_includes_key_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "--help"])
    assert result.exit_code == 0
    assert "create" in result.output
    assert "logs" in result.output


def test_notebook_help_includes_key_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["notebook", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "status" in result.output
    assert "top" in result.output


def test_resources_help_includes_key_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["resources", "--help"])
    assert result.exit_code == 0
    assert "list" in result.output
    assert "nodes" in result.output


def test_tunnel_help_includes_key_subcommands() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["tunnel", "--help"])
    assert result.exit_code == 0
    assert "add" in result.output
    assert "list" in result.output
    assert "migrate" in result.output
    assert "status" in result.output


def test_job_create_help_shows_fault_tolerant_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["job", "create", "--help"])
    assert result.exit_code == 0
    assert "--fault-tolerant" in result.output
    assert "--no-fault-tolerant" in result.output


def test_run_help_shows_fault_tolerant_flag() -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, ["run", "--help"])
    assert result.exit_code == 0
    assert "--fault-tolerant" in result.output
    assert "--no-fault-tolerant" in result.output


@pytest.mark.parametrize(
    "argv",
    [
        ["job", "create", "--help"],
        ["run", "--help"],
        ["notebook", "list", "--help"],
    ],
)
def test_workspace_help_mentions_account_scoped_aliases(argv: list[str]) -> None:
    runner = CliRunner()
    result = runner.invoke(cli_main, argv)
    assert result.exit_code == 0
    assert '[accounts."<username>".workspaces]' in result.output
    assert "--workspace-id" in result.output
