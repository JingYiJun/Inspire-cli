"""Runtime helpers shared by Inspire MCP tools."""

from __future__ import annotations

import shlex
import subprocess
from typing import Any

from inspire.bridge.tunnel import run_ssh_command
from inspire.bridge.tunnel.models import BridgeNotFoundError, TunnelNotAvailableError

from .errors import McpToolError


def build_exec_command(
    command: str,
    *,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """Build a shell command string with optional cwd/env prelude."""
    segments: list[str] = []
    if env:
        for key, value in env.items():
            segments.append(f"export {key}={shlex.quote(str(value))}")
    if cwd:
        segments.append(f"cd {shlex.quote(cwd)}")
    segments.append(command)
    return " && ".join(segments)


def exec_remote_command(
    *,
    bridge: str,
    command: str,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    timeout_s: int | None = None,
) -> dict[str, Any]:
    """Execute a command on a bridge and normalize the result."""
    full_command = build_exec_command(command, cwd=cwd, env=env)
    try:
        result = run_ssh_command(
            command=full_command,
            bridge_name=bridge,
            timeout=timeout_s,
            capture_output=True,
            check=False,
        )
    except BridgeNotFoundError as exc:
        raise McpToolError("bridge_not_found", str(exc), {"bridge": bridge}) from exc
    except TunnelNotAvailableError as exc:
        raise McpToolError("tunnel_unavailable", str(exc), {"bridge": bridge}) from exc
    except subprocess.TimeoutExpired as exc:
        raise McpToolError(
            "timeout",
            f"Command timed out after {exc.timeout}s",
            {"bridge": bridge, "timeout_s": exc.timeout},
        ) from exc
    except Exception as exc:  # noqa: BLE001
        raise McpToolError(
            "remote_command_failed",
            f"Remote command failed before execution: {exc}",
            {"bridge": bridge},
        ) from exc

    stdout = result.stdout or ""
    stderr = result.stderr or ""
    return {
        "ok": result.returncode == 0,
        "bridge": bridge,
        "cwd": cwd,
        "exit_code": result.returncode,
        "stdout": stdout,
        "stderr": stderr,
        "command": full_command,
    }
