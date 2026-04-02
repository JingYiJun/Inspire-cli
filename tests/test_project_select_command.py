"""Tests for the interactive project selection command."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from click.testing import CliRunner

from inspire.cli.context import EXIT_CONFIG_ERROR

select_module = importlib.import_module("inspire.cli.commands.project.select")
select_projects = select_module.select_projects


def _make_config(*, project_catalog=None, project_order=None, project_workdirs=None):
    return SimpleNamespace(
        project_catalog=project_catalog or {},
        project_order=project_order or [],
        project_workdirs=project_workdirs or {},
    )


class _Prompt:
    def __init__(self, response=None):
        self._response = response

    def ask(self):
        return self._response


def test_project_select_json_reports_current_order(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(project_order=["p-b", "p-a"]), {}),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)

    runner = CliRunner()
    result = runner.invoke(select_projects, ["--json"])

    assert result.exit_code == 0
    payload = json.loads(result.output)
    assert payload["project_order"] == ["p-b", "p-a"]
    assert payload["config_path"] == str(project_path)


def test_project_select_reset_clears_project_order_and_preserves_other_content(
    monkeypatch, tmp_path: Path
) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '# comment\n[defaults]\nproject_order = ["old"]\nresource = "1xH100"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(project_order=["old"]), {}),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)

    runner = CliRunner()
    result = runner.invoke(select_projects, ["--reset"])

    assert result.exit_code == 0
    content = project_path.read_text(encoding="utf-8")
    assert "# comment" in content
    assert 'resource = "1xH100"' in content
    assert "project_order" not in content


def test_project_select_requires_discovered_catalog(monkeypatch) -> None:
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (_make_config(), {}),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: None)

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == EXIT_CONFIG_ERROR
    assert "No projects found in the discovered project catalog" in result.output
    assert "init --discover" in result.output


def test_project_select_cancellation_does_not_write(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (
            _make_config(
                project_catalog={
                    "p-a": {"name": "Alpha"},
                    "p-b": {"name": "Beta"},
                }
            ),
            {},
        ),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)

    monkeypatch.setattr(select_module.questionary, "checkbox", lambda *a, **k: _Prompt())

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == 0
    assert "Project order updated successfully" not in result.output
    assert not project_path.exists()


def test_project_select_updates_project_order_with_ids(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    project_path.parent.mkdir(parents=True)
    project_path.write_text(
        '# keep\n[defaults]\nresource = "4CPU"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (
            _make_config(
                project_catalog={
                    "p-a": {"name": "Alpha"},
                    "p-b": {"name": "Beta"},
                    "p-c": {"name": "Gamma"},
                },
                project_order=["p-c"],
            ),
            {},
        ),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)

    select_responses = iter(["p-b"])

    monkeypatch.setattr(
        select_module.questionary, "checkbox", lambda *a, **k: _Prompt(["p-b", "p-a"])
    )
    monkeypatch.setattr(
        select_module.questionary, "select", lambda *a, **k: _Prompt(next(select_responses))
    )

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == 0
    assert "Project order updated successfully" in result.output
    content = project_path.read_text(encoding="utf-8")
    assert "# keep" in content
    assert 'resource = "4CPU"' in content
    assert 'project_order = ["Beta", "Alpha"]' in content


def test_project_select_prechecks_existing_name_and_id_entries(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (
            _make_config(
                project_catalog={
                    "p-a": {"name": "Alpha"},
                    "p-b": {"name": "Beta"},
                    "p-c": {"name": "Gamma"},
                },
                project_order=["Beta", "p-c"],
            ),
            {},
        ),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)

    captured_checked: dict[str, bool] = {}

    def fake_checkbox(*args, **kwargs):
        for choice in kwargs["choices"]:
            captured_checked[choice.value] = choice.checked
        return _Prompt()

    monkeypatch.setattr(select_module.questionary, "checkbox", fake_checkbox)

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == 0
    assert captured_checked == {"p-a": False, "p-b": True, "p-c": True}


def test_project_select_writes_id_for_duplicate_names(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (
            _make_config(
                project_catalog={
                    "p-a": {"name": "Shared"},
                    "p-b": {"name": "Shared"},
                }
            ),
            {},
        ),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)
    monkeypatch.setattr(select_module.questionary, "checkbox", lambda *a, **k: _Prompt(["p-b"]))

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == 0
    content = project_path.read_text(encoding="utf-8")
    assert 'project_order = ["p-b"]' in content


def test_project_select_writes_id_when_name_missing(monkeypatch, tmp_path: Path) -> None:
    project_path = tmp_path / ".inspire" / "config.toml"
    monkeypatch.setattr(
        select_module.Config,
        "from_files_and_env",
        lambda **_: (
            _make_config(
                project_catalog={
                    "p-a": {"name": ""},
                }
            ),
            {},
        ),
    )
    monkeypatch.setattr(select_module, "_find_project_config", lambda: project_path)
    monkeypatch.setattr(select_module.questionary, "checkbox", lambda *a, **k: _Prompt(["p-a"]))

    runner = CliRunner()
    result = runner.invoke(select_projects, [])

    assert result.exit_code == 0
    content = project_path.read_text(encoding="utf-8")
    assert 'project_order = ["p-a"]' in content
