"""Rsync-based sync helpers implemented over SSH tunnel access."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from .config import load_tunnel_config
from .models import TunnelConfig, TunnelError
from .ssh_exec import _build_ssh_process_env, _resolve_bridge_and_proxy, run_ssh_command

logger = logging.getLogger(__name__)


def _normalize_rsync_path(path: str) -> str:
    text = str(path).strip()
    if not text:
        return text
    return text if text.endswith("/") else text + "/"


def sync_via_rsync(
    source_dir: str,
    target_dir: str,
    *,
    bridge_name: Optional[str] = None,
    config: Optional[TunnelConfig] = None,
    timeout: int = 300,
    delete: bool = False,
) -> dict:
    """Sync a local directory to Bridge via rsync over the SSH tunnel."""
    source_path = Path(source_dir).expanduser()
    if not source_path.exists():
        return {
            "success": False,
            "error": f"Local source directory does not exist: {source_path}",
        }
    if not source_path.is_dir():
        return {
            "success": False,
            "error": f"Local source path is not a directory: {source_path}",
        }
    if shutil.which("rsync") is None:
        return {
            "success": False,
            "error": "rsync command not found on the local machine.",
        }

    if config is None:
        config = load_tunnel_config()

    try:
        _config, bridge, proxy_cmd = _resolve_bridge_and_proxy(bridge_name, config, quiet=True)
    except (TunnelError, OSError, ValueError) as error:
        return {
            "success": False,
            "error": str(error),
        }

    q_target_dir = shlex.quote(target_dir)
    preflight_cmd = f"""
set -e
command -v rsync >/dev/null 2>&1 || {{
  echo "rsync is not installed on Bridge." >&2
  exit 1
}}
mkdir -p {q_target_dir}
"""

    try:
        preflight = run_ssh_command(
            preflight_cmd.strip(),
            bridge_name=bridge.name,
            config=config,
            timeout=max(15, min(timeout, 60)),
            capture_output=True,
            check=False,
        )
    except (subprocess.SubprocessError, OSError, TunnelError, ValueError) as error:
        return {
            "success": False,
            "error": str(error),
        }

    if preflight.returncode != 0:
        error_msg = preflight.stderr.strip() or preflight.stdout.strip() or "Unknown error"
        return {
            "success": False,
            "error": error_msg,
        }

    ssh_command = shlex.join(
        [
            "ssh",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "BatchMode=yes",
            "-p",
            str(bridge.ssh_port),
        ]
    )

    remote_target = _normalize_rsync_path(target_dir)
    rsync_cmd = [
        "rsync",
        "-az",
        "--numeric-ids",
        "--exclude=.inspire/",
    ]
    if delete:
        rsync_cmd.append("--delete")
    rsync_cmd.extend(
        [
            "-e",
            ssh_command,
            _normalize_rsync_path(str(source_path.resolve())),
            f"{bridge.ssh_user}@localhost:{remote_target}",
        ]
    )

    logger.debug("sync_via_rsync bridge=%s delete=%s cmd=%s", bridge.name, delete, rsync_cmd)

    try:
        result = subprocess.run(
            rsync_cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=_build_ssh_process_env(),
        )
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "error": f"rsync command timed out after {timeout}s",
        }
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        return {
            "success": False,
            "error": str(error),
        }

    if result.returncode != 0:
        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return {
            "success": False,
            "error": error_msg,
        }

    return {
        "success": True,
        "synced_path": remote_target.rstrip("/"),
        "bridge_name": bridge.name,
        "delete": delete,
    }


__all__ = ["sync_via_rsync"]
