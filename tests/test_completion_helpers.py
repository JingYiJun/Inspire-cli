"""Tests for dynamic shell completion helpers."""

from __future__ import annotations

from types import SimpleNamespace

from click.shell_completion import CompletionItem
import click

from inspire.cli import completion as completion_module
from inspire.cli.commands.job.job_create import (
    _complete_project,
    _complete_resource,
    _complete_workspace,
    create as job_create_command,
)


def test_workspace_alias_completions_include_standard_and_configured_aliases(
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        workspaces={
            "cpu": "ws-cpu",
            "gpu": "ws-gpu",
            "internet": "ws-internet",
            "hpc": "ws-hpc",
        }
    )
    monkeypatch.setattr(
        completion_module.Config,
        "from_files_and_env",
        lambda **_: (config, []),
    )

    aliases = completion_module.get_workspace_alias_completions()

    assert aliases == ["cpu", "gpu", "hpc", "internet"]


def test_project_name_completions_include_catalog_ids_and_aliases(monkeypatch) -> None:
    config = SimpleNamespace(
        project_catalog={
            "proj-1": {"name": "Research Platform"},
            "proj-2": {"name": "A" * 50},
        },
        projects={"research": "proj-1"},
    )
    monkeypatch.setattr(
        completion_module.Config,
        "from_files_and_env",
        lambda **_: (config, []),
    )

    completions = completion_module.get_project_name_completions()
    by_value = {item.value: item for item in completions}

    assert by_value["proj-1"].help == "Research Platform"
    assert by_value["proj-2"].help.endswith("...")
    assert by_value["research"].help == "Alias for proj-1..."


def test_resource_spec_completions_include_common_and_configured_gpu_types(
    monkeypatch,
) -> None:
    config = SimpleNamespace(
        compute_groups=[
            {"gpu_type": "H200"},
            {"gpu_type": "RTX6000"},
            {"gpu_type": ""},
        ]
    )
    monkeypatch.setattr(
        completion_module.Config,
        "from_files_and_env",
        lambda **_: (config, []),
    )

    completions = completion_module.get_resource_spec_completions()
    values = {item.value for item in completions}

    assert "4CPU" in values
    assert "1xH200" in values
    assert "1xRTX6000" in values


def test_config_key_completions_return_completion_items() -> None:
    completions = completion_module.get_config_key_completions()

    assert completions
    assert all(isinstance(item, CompletionItem) for item in completions)


def test_job_create_options_are_wired_to_shell_completion_handlers() -> None:
    def _option(flag: str) -> click.Option:
        for param in job_create_command.params:
            if isinstance(param, click.Option) and flag in param.opts:
                return param
        raise AssertionError(f"Missing option {flag}")

    assert _option("--workspace")._custom_shell_complete is _complete_workspace
    assert _option("--project")._custom_shell_complete is _complete_project
    assert _option("--resource")._custom_shell_complete is _complete_resource
