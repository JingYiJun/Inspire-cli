"""Tests for config set command."""

import importlib.util
import os
import pty
import re
import select
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

_ROOT = Path(__file__).resolve().parent.parent

spec = importlib.util.spec_from_file_location(
    "set_cmd", str(_ROOT / "inspire/cli/commands/config/set_cmd.py")
)
set_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(set_module)

completion_spec = importlib.util.spec_from_file_location(
    "completion_module", str(_ROOT / "inspire/cli/completion.py")
)
completion_module = importlib.util.module_from_spec(completion_spec)
completion_spec.loader.exec_module(completion_module)

_CANCEL = set_module._MENU_CANCEL_SENTINEL
_BACK = set_module._MENU_BACK_SENTINEL
_ANSI_ESCAPE_RE = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\].*?(?:\x07|\x1b\\)|[@-_])")


def _opt(toml_key):
    return set_module.get_user_managed_option_by_toml(toml_key)


def _ordered_cats():
    cats = set_module.get_categories()
    user_opts = set_module.get_user_managed_options()
    cat_map: dict[str, list] = {}
    for o in user_opts:
        cat_map.setdefault(o.category, []).append(o)
    return [c for c in cats if c in cat_map] + [set_module._REMOTE_ENV_CATEGORY]


def _cat_idx(name):
    return _ordered_cats().index(name)


def _key_idx(category, toml_key):
    opts = [o for o in set_module.get_user_managed_options() if o.category == category]
    return opts.index(_opt(toml_key))


def _clean_terminal_output(data: bytes) -> str:
    return _ANSI_ESCAPE_RE.sub("", data.decode("utf-8", errors="replace").replace("\r", ""))


def _read_pty_until(master_fd: int, pattern: str, timeout: float = 3.0) -> str:
    deadline = time.time() + timeout
    buffer = b""
    cleaned = ""

    while time.time() < deadline:
        ready, _, _ = select.select([master_fd], [], [], 0.05)
        if not ready:
            continue
        try:
            chunk = os.read(master_fd, 65536)
        except OSError:
            break
        if not chunk:
            break
        buffer += chunk
        cleaned = _clean_terminal_output(buffer)
        if pattern in cleaned:
            return cleaned

    raise AssertionError(f"Timed out waiting for {pattern!r}. Last output:\n{cleaned}")


class TestConfigSetExplicit:
    def test_set_creates_new_config(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.target_dir", "/inspire/hdd/user/test"],
            )

        assert result.exit_code == 0
        assert config_path.exists()
        content = config_path.read_text()
        assert 'target_dir = "/inspire/hdd/user/test"' in content

    def test_set_updates_existing_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[defaults]\ntarget_dir = "/old/path"\n')

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.target_dir", "/new/path"],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert 'target_dir = "/new/path"' in content
        assert "/old/path" not in content

    def test_set_dry_run(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.target_dir", "/path", "--dry-run"],
            )

        assert result.exit_code == 0
        assert "Dry run" in result.output
        assert not config_path.exists()

    def test_set_global_config(self, tmp_path: Path):
        global_path = tmp_path / ".config" / "inspire" / "config.toml"

        with patch.object(
            set_module.Config, "resolve_global_config_path", return_value=global_path
        ):
            with patch.object(set_module, "_find_project_config", return_value=None):
                runner = CliRunner()
                result = runner.invoke(
                    set_module.set_config,
                    ["--global", "auth.username", "testuser"],
                )

        assert result.exit_code == 0
        assert global_path.exists()
        content = global_path.read_text()
        assert 'username = "testuser"' in content

    def test_set_validation_warning(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.priority", "not-a-number"],
            )

        assert result.exit_code == 0
        assert config_path.exists()

    def test_set_list_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.project_order", '["Project A", "Project B"]'],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "Project A" in content
        assert "Project B" in content

    def test_set_unknown_key_rejected(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["unknown.section.key", "value"],
            )

        assert result.exit_code != 0
        assert "Unknown config key" in result.output
        assert not config_path.exists()

    def test_set_discovery_owned_workspace_key_rejected(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["workspaces.cpu", "ws-123"],
            )

        assert result.exit_code != 0
        assert "discovery-owned" in result.output
        assert "init --discover" in result.output
        assert not config_path.exists()

    def test_set_deprecated_target_dir_key_rejected(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["paths.target_dir", "/legacy/path"],
            )

        assert result.exit_code != 0
        assert "defaults.target_dir" in result.output
        assert not config_path.exists()

    def test_set_preserves_comments(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"
        config_path.parent.mkdir(parents=True)
        original_content = """# This is a comment
[defaults]
target_dir = "/old/path"  # inline comment
"""
        config_path.write_text(original_content)

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.priority", "5"],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "# This is a comment" in content
        assert "# inline comment" in content
        assert "priority = 5" in content

    def test_set_boolean_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["api.skip_ssl_verify", "true"],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "skip_ssl_verify" in content

    def test_set_remote_env_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with patch.object(set_module, "_find_project_config", return_value=config_path):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["remote_env.PIP_INDEX_URL", "https://mirror.example/simple"],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "[remote_env]" in content
        assert 'PIP_INDEX_URL = "https://mirror.example/simple"' in content


class TestConfigSetSemiInteractive:
    def test_semi_interactive_prompts_and_saves(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        mock_text = MagicMock()
        mock_text.return_value.ask.return_value = "/new/path"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.target_dir"],
            )

        assert result.exit_code == 0
        content = config_path.read_text()
        assert 'target_dir = "/new/path"' in content

    def test_semi_interactive_cancel_does_not_write(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        mock_text = MagicMock()
        mock_text.return_value.ask.return_value = None

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(
                set_module.set_config,
                ["defaults.target_dir"],
            )

        assert result.exit_code == 0
        assert not config_path.exists()


class TestConfigSetInteractive:
    def test_interactive_flow_sets_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[0, _cat_idx("Defaults"), _key_idx("Defaults", "defaults.target_dir")],
            ),
            patch.object(
                set_module,
                "text",
                return_value=MagicMock(ask=MagicMock(return_value="/interactive/path")),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text()
        assert 'target_dir = "/interactive/path"' in content

    def test_interactive_cancel_at_scope(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(set_module, "_pick_menu", return_value=_CANCEL),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        assert not config_path.exists()

    def test_interactive_cancel_at_category(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(set_module, "_pick_menu", side_effect=[0, _CANCEL]),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        assert not config_path.exists()

    def test_interactive_cancel_at_key(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[0, _cat_idx("Defaults"), _CANCEL],
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        assert not config_path.exists()

    def test_interactive_cancel_at_value(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx("Defaults"),
                    _key_idx("Defaults", "defaults.target_dir"),
                    _CANCEL,
                ],
            ),
            patch.object(
                set_module, "text", return_value=MagicMock(ask=MagicMock(return_value=None))
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        assert not config_path.exists()

    def test_interactive_back_from_category_to_scope(self, tmp_path: Path):
        project_path = Path("/fake/project/.inspire/config.toml")
        global_path = tmp_path / "global_config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=project_path),
            patch.object(set_module.Config, "resolve_global_config_path", return_value=global_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _BACK,
                    1,
                    _cat_idx("Defaults"),
                    _key_idx("Defaults", "defaults.target_dir"),
                ],
            ),
            patch.object(
                set_module,
                "text",
                return_value=MagicMock(ask=MagicMock(return_value="/back-test")),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = global_path.read_text()
        assert 'target_dir = "/back-test"' in content

    def test_interactive_back_from_key_to_category(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx("Defaults"),
                    _BACK,
                    _cat_idx("API"),
                    _key_idx("API", "api.skip_ssl_verify"),
                ],
            ),
            patch.object(
                set_module,
                "text",
                return_value=MagicMock(ask=MagicMock(return_value="true")),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "skip_ssl_verify" in content

    def test_interactive_back_from_value_to_key(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        mock_text = MagicMock()
        mock_text.side_effect = [
            MagicMock(ask=MagicMock(return_value=None)),
            MagicMock(ask=MagicMock(return_value="7")),
        ]

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx("Defaults"),
                    _key_idx("Defaults", "defaults.target_dir"),
                    _key_idx("Defaults", "defaults.priority"),
                ],
            ),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text()
        assert "priority" in content
        assert "target_dir" not in content

    def test_interactive_back_from_value_restores_previous_key_cursor(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"
        recorded_calls = []
        responses = iter(
            [
                0,
                _cat_idx("Defaults"),
                _key_idx("Defaults", "defaults.target_dir"),
                _key_idx("Defaults", "defaults.priority"),
            ]
        )

        def fake_pick_menu(title, entries, cursor_index=0, **kwargs):
            recorded_calls.append((title, cursor_index, list(entries)))
            return next(responses)

        mock_text = MagicMock()
        mock_text.side_effect = [
            MagicMock(ask=MagicMock(return_value=None)),
            MagicMock(ask=MagicMock(return_value="7")),
        ]

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(set_module, "_pick_menu", side_effect=fake_pick_menu),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        setting_calls = [call for call in recorded_calls if call[0] == "Select a setting:"]
        assert len(setting_calls) == 2
        assert setting_calls[1][1] == _key_idx("Defaults", "defaults.target_dir")

    def test_interactive_single_option_category_can_back_to_category(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        mock_text = MagicMock()
        mock_text.side_effect = [
            MagicMock(ask=MagicMock(return_value=None)),
            MagicMock(ask=MagicMock(return_value="/single-option-back")),
        ]

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx("Sync"),
                    0,
                    _BACK,
                    _cat_idx("Defaults"),
                    _key_idx("Defaults", "defaults.target_dir"),
                ],
            ),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text()
        assert 'target_dir = "/single-option-back"' in content
        assert "default_remote" not in content

    def test_interactive_remote_env_back_from_value_returns_to_variable_list(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"
        config_path.parent.mkdir(parents=True)
        config_path.write_text('[remote_env]\nFOO = "bar"\n', encoding="utf-8")

        mock_text = MagicMock()
        mock_text.side_effect = [
            MagicMock(ask=MagicMock(return_value=None)),
            MagicMock(ask=MagicMock(return_value="NEW_VAR")),
            MagicMock(ask=MagicMock(return_value="baz")),
        ]

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx(set_module._REMOTE_ENV_CATEGORY),
                    0,
                    1,
                ],
            ),
            patch.object(set_module, "text", mock_text),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text(encoding="utf-8")
        assert 'FOO = "bar"' in content
        assert 'NEW_VAR = "baz"' in content

    def test_interactive_invalid_value_reprompts_in_place(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        mock_text = MagicMock()
        mock_text.side_effect = [
            MagicMock(ask=MagicMock(return_value="not-a-number")),
            MagicMock(ask=MagicMock(return_value="7")),
        ]

        with (
            patch.object(set_module, "_find_project_config", return_value=config_path),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    0,
                    _cat_idx("Defaults"),
                    _key_idx("Defaults", "defaults.priority"),
                ],
            ),
            patch.object(set_module, "text", mock_text),
            patch.object(
                set_module,
                "confirm",
                return_value=MagicMock(ask=MagicMock(return_value=False)),
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, [])

        assert result.exit_code == 0
        content = config_path.read_text(encoding="utf-8")
        assert "priority = 7" in content

    def test_interactive_menu_shows_schema_default_when_unset(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"
        recorded = []

        def fake_pick_menu(title, entries, cursor_index=0, **kwargs):
            recorded.append((title, list(entries)))
            if title == "Select a category:":
                return _cat_idx("API")
            return _CANCEL

        with (
            patch.object(set_module.Config, "resolve_global_config_path", return_value=config_path),
            patch.object(set_module, "_find_project_config", return_value=None),
            patch.object(set_module, "_pick_menu", side_effect=fake_pick_menu),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, ["--global"])

        assert result.exit_code == 0
        setting_entries = next(
            entries for title, entries in recorded if title == "Select a setting:"
        )
        assert "api.timeout  (30 (default))" in setting_entries
        assert "api.base_url  (https://api.example.com (default))" in setting_entries

    def test_interactive_current_prompt_shows_schema_default_when_unset(self, tmp_path: Path):
        config_path = tmp_path / ".inspire" / "config.toml"

        with (
            patch.object(set_module.Config, "resolve_global_config_path", return_value=config_path),
            patch.object(set_module, "_find_project_config", return_value=None),
            patch.object(
                set_module,
                "_pick_menu",
                side_effect=[
                    _cat_idx("API"),
                    _key_idx("API", "api.timeout"),
                    _CANCEL,
                ],
            ),
            patch.object(
                set_module, "text", return_value=MagicMock(ask=MagicMock(return_value=None))
            ),
        ):
            runner = CliRunner()
            result = runner.invoke(set_module.set_config, ["--global"])

        assert result.exit_code == 0
        assert "Current: 30 (default)" in result.output


CI_SKIP = (
    os.name == "nt"
    or os.environ.get("CI") is not None
    or os.environ.get("CODERIDGE_RUNNER") is not None
)


@pytest.mark.skipif(CI_SKIP, reason="PTY-based terminal test (flaky in CI)")
def test_interactive_left_and_right_arrows_work_in_real_tty(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()

    master_fd, slave_fd = pty.openpty()
    env = os.environ.copy()
    env["HOME"] = str(home)
    env["TERM"] = "xterm-256color"

    repo_root = Path(__file__).resolve().parent.parent
    proc = subprocess.Popen(
        [str(repo_root / "bin" / "inspire"), "config", "set"],
        stdin=slave_fd,
        stdout=slave_fd,
        stderr=slave_fd,
        cwd=str(repo_root),
        env=env,
    )
    os.close(slave_fd)

    try:
        initial = _read_pty_until(master_fd, "Which config file?")
        assert "» Project" in initial
        assert "Cancel" not in initial
        assert "← Back" not in initial

        os.write(master_fd, b"\x1b[B")
        os.write(master_fd, b"\x1b[C")
        into_category = _read_pty_until(master_fd, "Select a category:")
        assert "Which config file?" not in into_category
        assert "doesn't support cursor position requests" not in into_category
        assert "Cancel" not in into_category
        assert "← Back" not in into_category

        os.write(master_fd, b"\x1b[B")
        after_down = _read_pty_until(master_fd, "» API")
        assert "Cancel" not in after_down

        os.write(master_fd, b"\x1b[C")
        into_api = _read_pty_until(master_fd, "Select a setting:")
        assert "api.base_url" in into_api
        assert "Select a category:" not in into_api
        assert "Which config file?" not in into_api

        os.write(master_fd, b"\x1b[D")
        back_to_category = _read_pty_until(master_fd, "Select a category:")
        assert "» API" in back_to_category
        assert "Select a setting:" not in back_to_category
        assert "Which config file?" not in back_to_category
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)
        os.close(master_fd)


class TestCompletions:
    def test_config_key_completions_exclude_discovery_owned_and_deprecated_keys(self):
        completion_values = {item.value for item in completion_module.get_config_key_completions()}

        assert "defaults.target_dir" in completion_values
        assert "workspaces.cpu" not in completion_values
        assert "paths.target_dir" not in completion_values
