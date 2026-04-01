"""Rsync-based sync helpers implemented over SSH tunnel access."""

from __future__ import annotations

import logging
import shlex
import shutil
import subprocess
from pathlib import Path, PurePosixPath
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


def _remote_parent_dir(path: str) -> str:
    text = str(path).strip()
    if not text:
        return text
    parent = str(PurePosixPath(text).parent)
    return parent or "."


def _is_explicit_directory_target(path: str) -> bool:
    return str(path).strip().endswith("/")


def _ensure_local_path_ready(
    *,
    target_path: Path,
    source_kind: str,
    mkdir_parent: bool,
) -> Optional[str]:
    if source_kind == "directory":
        if target_path.exists() and not target_path.is_dir():
            return f"Local target path is not a directory: {target_path}"
        if not target_path.exists():
            if not mkdir_parent:
                return f"Local target directory does not exist: {target_path}"
            target_path.mkdir(parents=True, exist_ok=True)
        return None

    if _is_explicit_directory_target(str(target_path)):
        return f"Local target file path must not end with '/': {target_path}"
    if target_path.exists() and target_path.is_dir():
        return f"Local target file path points to a directory: {target_path}"
    parent = target_path.parent
    if parent.exists():
        return None
    if not mkdir_parent:
        return f"Local target parent directory does not exist: {parent}"
    parent.mkdir(parents=True, exist_ok=True)
    return None


def _build_push_preflight_command(
    *,
    target_path: str,
    source_kind: str,
    mkdir_parent: bool,
) -> str:
    quoted_target = shlex.quote(target_path)
    lines = [
        "set -e",
        "command -v rsync >/dev/null 2>&1 || {",
        '  echo "rsync is not installed on Bridge." >&2',
        "  exit 1",
        "}",
    ]
    if source_kind == "directory" or _is_explicit_directory_target(target_path):
        if mkdir_parent:
            lines.append(f"mkdir -p {quoted_target}")
        else:
            lines.extend(
                [
                    f"test -d {quoted_target} || {{",
                    f'  echo "Remote target directory does not exist: {target_path}" >&2',
                    "  exit 1",
                    "}",
                ]
            )
    else:
        parent = _remote_parent_dir(target_path)
        quoted_parent = shlex.quote(parent)
        if mkdir_parent:
            lines.append(f"mkdir -p {quoted_parent}")
        else:
            lines.extend(
                [
                    f"test -d {quoted_parent} || {{",
                    f'  echo "Remote target parent directory does not exist: {parent}" >&2',
                    "  exit 1",
                    "}",
                ]
            )
    return "\n".join(lines)


def _build_pull_preflight_command(source_path: str) -> str:
    quoted_source = shlex.quote(source_path)
    return "\n".join(
        [
            "set -e",
            "command -v rsync >/dev/null 2>&1 || {",
            '  echo "rsync is not installed on Bridge." >&2',
            "  exit 1",
            "}",
            f"if test -d {quoted_source}; then",
            '  echo "directory"',
            f"elif test -f {quoted_source}; then",
            '  echo "file"',
            "else",
            f'  echo "Remote source path does not exist: {source_path}" >&2',
            "  exit 1",
            "fi",
        ]
    )


def _build_rsync_ssh_command(bridge, proxy_cmd: str) -> str:
    return shlex.join(
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


def _extract_error_message(result: subprocess.CompletedProcess) -> str:
    return result.stderr.strip() or result.stdout.strip() or "Unknown error"


def sync_paths_via_rsync(
    source_path: str,
    target_path: str,
    *,
    direction: str,
    bridge_name: Optional[str] = None,
    config: Optional[TunnelConfig] = None,
    timeout: int = 300,
    delete: bool = False,
    mkdir_parent: bool = True,
    exclude: Optional[list[str]] = None,
) -> dict:
    """Sync files or directories between local disk and Bridge via rsync."""
    direction_text = str(direction).strip().lower()
    if direction_text not in {"push", "pull"}:
        return {
            "success": False,
            "error": f"Unsupported sync direction: {direction}",
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

    source_kind = ""
    local_source_path: Optional[Path] = None
    local_target_path: Optional[Path] = None

    if direction_text == "push":
        local_source_path = Path(source_path).expanduser()
        if not local_source_path.exists():
            return {
                "success": False,
                "error": f"Local source path does not exist: {local_source_path}",
            }
        source_kind = "directory" if local_source_path.is_dir() else "file"
        preflight_cmd = _build_push_preflight_command(
            target_path=target_path,
            source_kind=source_kind,
            mkdir_parent=mkdir_parent,
        )
    else:
        local_target_path = Path(target_path).expanduser()
        preflight_cmd = _build_pull_preflight_command(source_path)

    try:
        preflight = run_ssh_command(
            preflight_cmd,
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
        return {
            "success": False,
            "error": _extract_error_message(preflight),
        }

    if direction_text == "pull":
        source_kind = (preflight.stdout or "").strip().splitlines()[-1].strip().lower()
        if source_kind not in {"file", "directory"}:
            return {
                "success": False,
                "error": f"Unable to determine remote source type for: {source_path}",
            }
        assert local_target_path is not None
        local_ready_error = _ensure_local_path_ready(
            target_path=local_target_path,
            source_kind=source_kind,
            mkdir_parent=mkdir_parent,
        )
        if local_ready_error:
            return {
                "success": False,
                "error": local_ready_error,
            }

    ssh_command = _build_rsync_ssh_command(bridge, proxy_cmd)
    rsync_cmd = ["rsync", "-az", "--numeric-ids"]
    if source_kind == "directory":
        for pattern in exclude or [".inspire/"]:
            rsync_cmd.append(f"--exclude={pattern}")
        if delete:
            rsync_cmd.append("--delete")

    if direction_text == "push":
        assert local_source_path is not None
        source_arg = (
            _normalize_rsync_path(str(local_source_path.resolve()))
            if source_kind == "directory"
            else str(local_source_path.resolve())
        )
        remote_arg = (
            _normalize_rsync_path(target_path)
            if source_kind == "directory" or _is_explicit_directory_target(target_path)
            else target_path
        )
        source_arg_final = source_arg
        target_arg_final = f"{bridge.ssh_user}@localhost:{remote_arg}"
    else:
        assert local_target_path is not None
        remote_source = (
            _normalize_rsync_path(source_path) if source_kind == "directory" else source_path
        )
        local_target = (
            _normalize_rsync_path(str(local_target_path.resolve()))
            if source_kind == "directory"
            else str(local_target_path.resolve())
        )
        source_arg_final = f"{bridge.ssh_user}@localhost:{remote_source}"
        target_arg_final = local_target

    rsync_cmd.extend(["-e", ssh_command, source_arg_final, target_arg_final])

    logger.debug(
        "sync_paths_via_rsync bridge=%s direction=%s delete=%s cmd=%s",
        bridge.name,
        direction_text,
        delete,
        rsync_cmd,
    )

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
        return {
            "success": False,
            "error": _extract_error_message(result),
        }

    return {
        "success": True,
        "bridge_name": bridge.name,
        "direction": direction_text,
        "source_path": source_path,
        "target_path": target_path,
        "source_kind": source_kind,
        "delete": bool(delete and source_kind == "directory"),
        "method": "rsync",
        "stdout": result.stdout or "",
        "stderr": result.stderr or "",
    }


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
    result = sync_paths_via_rsync(
        source_path=source_dir,
        target_path=target_dir,
        direction="push",
        bridge_name=bridge_name,
        config=config,
        timeout=timeout,
        delete=delete,
        mkdir_parent=True,
        exclude=[".inspire/"],
    )
    if not result.get("success"):
        return result
    result["synced_path"] = str(target_dir).rstrip("/")
    return result


__all__ = ["sync_paths_via_rsync", "sync_via_rsync"]
