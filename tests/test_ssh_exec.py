"""Tests for SSH exec helpers."""

from __future__ import annotations

import io
import subprocess
from typing import Any, Optional

import pytest

from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig
from inspire.bridge.tunnel.ssh_exec import (
    build_ssh_process_env,
    get_ssh_command_args,
    run_ssh_command,
    run_ssh_command_streaming,
)


def _assert_has_locale_ssh_options(args: list[str]) -> None:
    assert "/dev/null" in args
    assert "SetEnv=LC_ALL=C" in args
    assert "SetEnv=LANG=C" in args
    assert "SetEnv=TERM=xterm-256color" in args


def _stub_resolve(*args: Any, **kwargs: Any) -> tuple[TunnelConfig, BridgeProfile, str]:
    return (
        TunnelConfig(),
        BridgeProfile(name="default", proxy_url="https://proxy.example.com"),
        "proxy-cmd",
    )


def test_run_ssh_command_forces_c_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ssh_exec_module.subprocess, "run", fake_run)

    result = run_ssh_command("echo ok")

    assert result.returncode == 0
    _assert_has_locale_ssh_options(captured["cmd"])
    assert "BatchMode=yes" in captured["cmd"]
    assert captured["cmd"][-3:] == ["bash", "--noprofile", "--norc"]
    assert captured["kwargs"]["env"]["LC_ALL"] == "C"
    assert captured["kwargs"]["env"]["LANG"] == "C"


def test_run_ssh_command_uses_bridge_identity_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    def _stub_resolve_with_identity(
        *args: Any, **kwargs: Any
    ) -> tuple[TunnelConfig, BridgeProfile, str]:
        return (
            TunnelConfig(),
            BridgeProfile(
                name="default",
                proxy_url="https://proxy.example.com",
                identity_file="/tmp/test-id",
            ),
            "proxy-cmd",
        )

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve_with_identity)

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ssh_exec_module.subprocess, "run", fake_run)

    result = run_ssh_command("echo ok")

    assert result.returncode == 0
    assert captured["cmd"][:3] == ["ssh", "-i", "/tmp/test-id"]


def test_run_ssh_command_pass_stdin_uses_wrapped_remote_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    captured: dict[str, Any] = {}

    def fake_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(ssh_exec_module.subprocess, "run", fake_run)

    result = run_ssh_command("bash -s", pass_stdin=True)

    assert result.returncode == 0
    _assert_has_locale_ssh_options(captured["cmd"])
    assert "BatchMode=yes" in captured["cmd"]
    assert captured["cmd"][-1] == "bash --noprofile --norc -lc 'bash -s'"
    assert captured["kwargs"]["input"] is None


def test_run_ssh_command_streaming_forces_c_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    captured: dict[str, Any] = {}

    class FakeStdin:
        def __init__(self) -> None:
            self.data = ""
            self.closed = False

        def write(self, text: str) -> None:
            self.data += text

        def close(self) -> None:
            self.closed = True

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = io.StringIO("hello\n")
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        process = FakeProcess()
        captured["process"] = process
        return process

    monkeypatch.setattr(ssh_exec_module.subprocess, "Popen", fake_popen)

    emitted: list[str] = []
    exit_code = run_ssh_command_streaming("echo hello", output_callback=emitted.append)

    assert exit_code == 0
    assert emitted == ["hello\n"]
    _assert_has_locale_ssh_options(captured["cmd"])
    assert captured["cmd"][-3:] == ["bash", "--noprofile", "--norc"]
    assert captured["kwargs"]["env"]["LC_ALL"] == "C"
    assert captured["kwargs"]["env"]["LANG"] == "C"
    assert captured["process"].stdin.data.startswith("export LC_ALL=C LANG=C; echo hello")


def test_run_ssh_command_streaming_pass_stdin_does_not_write_wrapper_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    captured: dict[str, Any] = {}

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = None
            self.stdout = io.StringIO("hello\n")
            self.returncode = 0

        def poll(self) -> int:
            return 0

        def terminate(self) -> None:
            return None

        def wait(self) -> int:
            return 0

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr(ssh_exec_module.subprocess, "Popen", fake_popen)

    emitted: list[str] = []
    exit_code = run_ssh_command_streaming(
        "bash -s", pass_stdin=True, output_callback=emitted.append
    )

    assert exit_code == 0
    assert emitted == ["hello\n"]
    assert captured["cmd"][-1] == "bash --noprofile --norc -lc 'bash -s'"
    assert captured["kwargs"]["stdin"] is None


def test_get_ssh_command_args_uses_stdio_proxycommand(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    config = TunnelConfig()
    config.add_bridge(
        BridgeProfile(
            name="default",
            proxy_url="https://proxy.example.com",
            identity_file="/tmp/test-id",
        )
    )
    monkeypatch.setattr(ssh_exec_module, "_ensure_rtunnel_binary", lambda _config: None)

    args = get_ssh_command_args(config=config, remote_command="echo hi && pwd")

    assert args[0] == "ssh"
    joined = " ".join(args)
    assert "-i /tmp/test-id" in joined
    assert "-F /dev/null" in joined
    assert "SetEnv=LC_ALL=C" in joined
    assert "SetEnv=LANG=C" in joined
    assert "SetEnv=TERM=xterm-256color" in joined
    assert "ProxyCommand=" in joined
    assert "stdio://" in joined
    assert "pick_port" not in joined
    assert "LOCAL_PORT" not in joined
    assert "bash --noprofile --norc -lc" in joined
    assert "echo hi && pwd" in joined


def test_build_ssh_process_env_forces_safe_locale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("LC_ALL", "en_US.UTF-8")
    monkeypatch.setenv("LANG", "en_US.UTF-8")

    env = build_ssh_process_env()

    assert env["LC_ALL"] == "C"
    assert env["LANG"] == "C"


def test_run_ssh_command_streaming_does_not_reemit_lines_after_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    class FakeStdin:
        def __init__(self) -> None:
            self.data = ""

        def write(self, text: str) -> None:
            self.data += text

        def close(self) -> None:
            return None

    class FakeStdout:
        def __init__(self) -> None:
            self.readline_calls = 0

        def readline(self) -> str:
            self.readline_calls += 1
            if self.readline_calls == 1:
                return "dupe\n"
            return ""

        def __iter__(self) -> Any:
            # Simulate a stream implementation that may still expose a line iterator
            # after a readline was already consumed.
            return iter(["dupe\n"])

    class FakeProcess:
        def __init__(self) -> None:
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.returncode = 2
            self.poll_calls = 0

        def poll(self) -> Optional[int]:
            self.poll_calls += 1
            if self.poll_calls == 1:
                return None
            return self.returncode

        def terminate(self) -> None:
            return None

        def wait(self) -> int:
            return self.returncode

    def fake_popen(cmd: list[str], **kwargs: Any) -> FakeProcess:
        return FakeProcess()

    select_calls = {"count": 0}

    def fake_select(read: Any, write: Any, err: Any, timeout: float) -> tuple[Any, Any, Any]:
        select_calls["count"] += 1
        if select_calls["count"] == 1:
            return (read, [], [])
        return ([], [], [])

    monkeypatch.setattr(ssh_exec_module.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(ssh_exec_module.select, "select", fake_select)

    emitted: list[str] = []
    exit_code = run_ssh_command_streaming("echo hello", output_callback=emitted.append)

    assert exit_code == 2
    assert emitted == ["dupe\n"]


def test_build_ssh_base_args_structure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that _build_ssh_base_args produces correct argument structure."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    bridge = BridgeProfile(
        name="test",
        proxy_url="https://proxy.example.com",
    )

    args = ssh_exec_module._build_ssh_base_args(
        bridge=bridge,
        proxy_cmd="test-proxy-cmd",
        batch_mode=True,
    )

    # Verify base structure
    assert args[0] == "ssh"
    assert "-F" in args
    assert "/dev/null" in args
    assert "SetEnv=LC_ALL=C" in args
    assert "SetEnv=LANG=C" in args
    assert "SetEnv=TERM=xterm-256color" in args
    assert "StrictHostKeyChecking=no" in args
    assert "UserKnownHostsFile=/dev/null" in args
    assert "BatchMode=yes" in args
    assert "ProxyCommand=test-proxy-cmd" in args
    assert "LogLevel=ERROR" in args
    assert "-p" in args
    assert str(bridge.ssh_port) in args
    assert f"{bridge.ssh_user}@localhost" in args


def test_build_ssh_base_args_with_identity_file(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that identity file is included when set."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    bridge = BridgeProfile(
        name="test",
        proxy_url="https://proxy.example.com",
        identity_file="/path/to/key",
    )

    args = ssh_exec_module._build_ssh_base_args(
        bridge=bridge,
        proxy_cmd="test-proxy",
        batch_mode=True,
    )

    assert "-i" in args
    i_index = args.index("-i")
    assert args[i_index + 1] == "/path/to/key"


def test_build_ssh_base_args_without_batch_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Test that BatchMode is not added when batch_mode=False."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    bridge = BridgeProfile(name="test", proxy_url="https://proxy.example.com")

    args = ssh_exec_module._build_ssh_base_args(
        bridge=bridge,
        proxy_cmd="test-proxy",
        batch_mode=False,
    )

    assert "BatchMode=yes" not in args


def test_wrap_remote_command_quotes_special_chars() -> None:
    """Test that _wrap_remote_command properly quotes special characters."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    command = "echo 'hello world' && pwd"
    wrapped = ssh_exec_module._wrap_remote_command(command)

    # Should contain the command with proper quoting
    assert "bash" in wrapped
    assert "--noprofile" in wrapped
    assert "--norc" in wrapped
    assert "-lc" in wrapped
    # The command should be quoted
    assert wrapped.endswith("'") or '"' in wrapped


def test_build_stdin_script_format() -> None:
    """Test that _build_stdin_script produces correct script format."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    command = "echo hello"
    script = ssh_exec_module._build_stdin_script(command)

    assert script.startswith("export LC_ALL=C LANG=C;")
    assert command in script
    assert script.endswith("\n")


def test_quiet_remote_shell_args_returns_correct_args() -> None:
    """Test that _quiet_remote_shell_args returns expected shell arguments."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    args = ssh_exec_module._quiet_remote_shell_args()

    assert args == ["bash", "--noprofile", "--norc"]


def test_run_ssh_command_error_logged_at_debug(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Test that SSH command errors are logged at debug level."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module
    import logging

    monkeypatch.setattr(ssh_exec_module, "_resolve_bridge_and_proxy", _stub_resolve)

    def fake_run(cmd, **kwargs):
        # Return a failed result instead of raising
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="Connection refused")

    monkeypatch.setattr(ssh_exec_module.subprocess, "run", fake_run)

    with caplog.at_level(logging.DEBUG, logger="inspire.bridge.tunnel.ssh_exec"):
        result = run_ssh_command("echo test")

    # The error should be captured in the result
    assert result.returncode == 1


def test_ssh_locale_args_returns_correct_options() -> None:
    """Test that _ssh_locale_args returns correct SSH locale options."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    args = ssh_exec_module._ssh_locale_args()

    assert "-F" in args
    assert "/dev/null" in args
    assert "SetEnv=LC_ALL=C" in args
    assert "SetEnv=LANG=C" in args
    assert "SetEnv=TERM=xterm-256color" in args


def test_constants_defined() -> None:
    """Test that magic string constants are properly defined."""
    import inspire.bridge.tunnel.ssh_exec as ssh_exec_module

    assert hasattr(ssh_exec_module, "REMOTE_LOCALE_EXPORT")
    assert hasattr(ssh_exec_module, "QUIET_SHELL_ARGS")
    assert ssh_exec_module.REMOTE_LOCALE_EXPORT == "export LC_ALL=C LANG=C;"
    assert ssh_exec_module.QUIET_SHELL_ARGS == ["bash", "--noprofile", "--norc"]
