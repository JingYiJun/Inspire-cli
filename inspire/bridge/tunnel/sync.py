"""Rsync-based sync helpers implemented over SSH tunnel access."""

from __future__ import annotations

import contextlib
import logging
import os
import re
import shlex
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Optional

from .config import load_tunnel_config
from .models import TunnelConfig, TunnelError
from .scp import run_scp_transfer
from .ssh_exec import build_ssh_process_env, _resolve_bridge_and_proxy, run_ssh_command

_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
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


def _extract_sha(stdout: str) -> Optional[str]:
    """Extract the last full SHA line from command output."""
    for line in reversed([ln.strip().lower() for ln in stdout.splitlines() if ln.strip()]):
        if _SHA_RE.match(line):
            return line
    return None


def _git_command_success(args: list[str]) -> bool:
    """Run a local git command and return True when it succeeds."""
    try:
        result = subprocess.run(
            args,
            check=False,
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except (subprocess.SubprocessError, OSError) as error:
        logger.debug("Local git command failed for %s: %s", args, error)
        return False


def _git_rev_count(revision_range: str) -> Optional[int]:
    """Count commits in a revision range, returning None on errors."""
    try:
        result = subprocess.run(
            ["git", "rev-list", "--count", revision_range],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return None
        return int((result.stdout or "").strip())
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        logger.debug("Unable to count revisions for %s: %s", revision_range, error)
        return None


def _create_git_bundle(bundle_file: str, revision: str) -> Optional[str]:
    """Create a git bundle and return an error message on failure."""
    try:
        subprocess.run(
            ["git", "bundle", "create", bundle_file, revision],
            check=True,
            capture_output=True,
            text=True,
        )
        return None
    except subprocess.CalledProcessError as error:
        return error.stderr.strip() or error.stdout.strip() or str(error)


def _probe_remote_branch_tip(
    *,
    target_dir: str,
    branch: str,
    bridge_name: Optional[str],
    config: TunnelConfig,
    timeout: int,
) -> Optional[str]:
    """Best-effort probe for the current remote branch tip SHA."""
    q_target_dir = shlex.quote(target_dir)
    q_branch = shlex.quote(branch)

    probe_cmd = f"""
set -e
cd {q_target_dir}
if [ ! -d .git ]; then
  exit 0
fi
branch={q_branch}
git rev-parse --verify "refs/heads/$branch" 2>/dev/null || true
"""

    try:
        probe = run_ssh_command(
            probe_cmd.strip(),
            bridge_name=bridge_name,
            config=config,
            timeout=max(15, min(timeout, 30)),
            capture_output=True,
            check=False,
        )
    except (subprocess.SubprocessError, OSError, TunnelError) as error:
        logger.debug("Failed to probe remote branch tip for %s/%s: %s", target_dir, branch, error)
        return None

    if probe.returncode != 0:
        return None
    return _extract_sha(probe.stdout or "")


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
            env=build_ssh_process_env(),
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


def sync_via_ssh(
    target_dir: str,
    branch: str,
    commit_sha: str,
    remote: str = "origin",
    force: bool = False,
    bridge_name: Optional[str] = None,
    config: Optional[TunnelConfig] = None,
    timeout: int = 60,
) -> dict:
    """Sync code on Bridge via SSH ProxyCommand."""
    if config is None:
        config = load_tunnel_config()

    q_target_dir = shlex.quote(target_dir)
    q_branch = shlex.quote(branch)
    q_remote = shlex.quote(remote)
    q_commit_sha = shlex.quote(commit_sha)

    update_cmd = (
        f"git reset --hard {q_commit_sha}" if force else f"git merge --ff-only {q_commit_sha}"
    )
    sync_cmd = f"""
set -e
cd {q_target_dir}
git fetch {q_remote} {q_branch}
git checkout {q_branch}
{update_cmd}
expected_sha={q_commit_sha}
actual_sha="$(git rev-parse HEAD)"
if [ "$actual_sha" != "$expected_sha" ]; then
  echo "Expected $expected_sha but got $actual_sha" >&2
  exit 1
fi
printf '%s\\n' "$actual_sha"
"""

    try:
        result = run_ssh_command(
            sync_cmd.strip(),
            bridge_name=bridge_name,
            config=config,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            synced_sha = lines[-1].strip() if lines else ""
            return {
                "success": True,
                "synced_sha": synced_sha,
                "error": None,
            }

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return {
            "success": False,
            "synced_sha": None,
            "error": error_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "synced_sha": None,
            "error": f"Sync command timed out after {timeout}s",
        }
    except (subprocess.SubprocessError, OSError, TunnelError, ValueError) as error:
        return {
            "success": False,
            "synced_sha": None,
            "error": str(error),
        }


def sync_via_ssh_bundle(
    target_dir: str,
    branch: str,
    commit_sha: str,
    force: bool = False,
    bridge_name: Optional[str] = None,
    config: Optional[TunnelConfig] = None,
    timeout: int = 120,
) -> dict:
    """Sync code to Bridge via SSH tunnel using a local git bundle."""
    if config is None:
        config = load_tunnel_config()

    bundle_file = None
    bundle_mode = "full"
    remote_base_sha = _probe_remote_branch_tip(
        target_dir=target_dir,
        branch=branch,
        bridge_name=bridge_name,
        config=config,
        timeout=timeout,
    )

    if remote_base_sha and remote_base_sha == commit_sha.lower():
        return {
            "success": True,
            "synced_sha": commit_sha.lower(),
            "error": None,
            "bundle_mode": "up_to_date",
        }

    bundle_rev = "HEAD"
    if remote_base_sha:
        has_remote_base = _git_command_success(
            ["git", "cat-file", "-e", f"{remote_base_sha}^{{commit}}"]
        )
        is_ancestor = _git_command_success(
            ["git", "merge-base", "--is-ancestor", remote_base_sha, commit_sha]
        )
        if has_remote_base and is_ancestor:
            incremental_range = f"{remote_base_sha}..{commit_sha}"
            incremental_count = _git_rev_count(incremental_range)
            if incremental_count == 0:
                return {
                    "success": True,
                    "synced_sha": commit_sha.lower(),
                    "error": None,
                    "bundle_mode": "up_to_date",
                    "bundle_base_sha": remote_base_sha,
                }
            bundle_mode = "incremental"
            bundle_rev = incremental_range

    try:
        with tempfile.NamedTemporaryFile(
            prefix="inspire-sync-",
            suffix=".bundle",
            delete=False,
        ) as tmp:
            bundle_file = tmp.name

        error_msg = _create_git_bundle(bundle_file, bundle_rev)
        if error_msg and bundle_mode == "incremental":
            incremental_count = _git_rev_count(bundle_rev)
            if incremental_count == 0:
                return {
                    "success": True,
                    "synced_sha": commit_sha.lower(),
                    "error": None,
                    "bundle_mode": "up_to_date",
                    "bundle_base_sha": remote_base_sha,
                }

            full_error = _create_git_bundle(bundle_file, "HEAD")
            if full_error is None:
                bundle_mode = "full"
                bundle_rev = "HEAD"
                error_msg = None
            else:
                error_msg = full_error

        if error_msg:
            return {
                "success": False,
                "synced_sha": None,
                "error": f"Failed to create git bundle: {error_msg}",
            }

        remote_bundle = f"/tmp/{os.path.basename(bundle_file)}"
        scp_result = run_scp_transfer(
            local_path=bundle_file,
            remote_path=remote_bundle,
            download=False,
            bridge_name=bridge_name,
            config=config,
            timeout=timeout,
        )
        if scp_result.returncode != 0:
            return {
                "success": False,
                "synced_sha": None,
                "error": f"Failed to upload git bundle (scp exit {scp_result.returncode})",
            }

        q_target_dir = shlex.quote(target_dir)
        q_branch = shlex.quote(branch)
        q_commit_sha = shlex.quote(commit_sha)
        q_remote_bundle = shlex.quote(remote_bundle)

        update_cmd = (
            f"git reset --hard {q_commit_sha}" if force else f"git merge --ff-only {q_commit_sha}"
        )
        sync_cmd = f"""
set -e
trap 'rm -f {q_remote_bundle}' EXIT
cd {q_target_dir}
if [ ! -d .git ]; then
  echo "Target directory is not a git repository: {q_target_dir}" >&2
  exit 1
fi
git fetch {q_remote_bundle} {q_commit_sha}
git checkout {q_branch} || git checkout -b {q_branch}
{update_cmd}
expected_sha={q_commit_sha}
actual_sha="$(git rev-parse HEAD)"
if [ "$actual_sha" != "$expected_sha" ]; then
  echo "Expected $expected_sha but got $actual_sha" >&2
  exit 1
fi
printf '%s\\n' "$actual_sha"
"""

        result = run_ssh_command(
            sync_cmd.strip(),
            bridge_name=bridge_name,
            config=config,
            timeout=timeout,
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            lines = result.stdout.strip().split("\n")
            synced_sha = lines[-1].strip() if lines else ""
            return {
                "success": True,
                "synced_sha": synced_sha,
                "error": None,
                "bundle_mode": bundle_mode,
                "bundle_base_sha": remote_base_sha,
            }

        error_msg = result.stderr.strip() or result.stdout.strip() or "Unknown error"
        return {
            "success": False,
            "synced_sha": None,
            "error": error_msg,
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "synced_sha": None,
            "error": f"Offline sync command timed out after {timeout}s",
        }
    except (subprocess.SubprocessError, OSError, TunnelError, ValueError) as error:
        return {
            "success": False,
            "synced_sha": None,
            "error": str(error),
        }
    finally:
        if bundle_file:
            with contextlib.suppress(OSError):
                os.unlink(bundle_file)


__all__ = [
    "sync_paths_via_rsync",
    "sync_via_rsync",
    "sync_via_ssh",
    "sync_via_ssh_bundle",
]
