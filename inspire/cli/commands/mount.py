"""Mount command -- mount Inspire shared filesystem locally using rclone.

Works by establishing a persistent SSH tunnel via a bridge profile,
then mounting the remote filesystem through that tunnel with rclone.

Requirements:
  - rclone >= 1.60 installed locally
  - A configured bridge with a running notebook (inspire notebook ssh --save-as)
  - SFTP subsystem available on the remote (auto-installed via 'inspire mount setup')
"""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

import click

from inspire.bridge.tunnel import load_tunnel_config
from inspire.bridge.tunnel.models import BridgeProfile, TunnelConfig
from inspire.bridge.tunnel.ssh import _get_proxy_command, _test_ssh_connection
from inspire.cli.context import (
    Context,
    EXIT_CONFIG_ERROR,
    EXIT_GENERAL_ERROR,
    pass_context,
)
from inspire.cli.utils.errors import exit_with_error as _handle_error
from inspire.cli.utils.output import emit_info, emit_success, emit_warning
from inspire.config import Config, ConfigError

MOUNT_STATE_FILE = Path.home() / ".config" / "inspire" / "mount_state.json"
DEFAULT_MOUNT_POINT = Path.home() / "inspire"
DEFAULT_DIR_CACHE_TIME = "30s"
DEFAULT_ATTR_TIMEOUT = "5s"
DEFAULT_DAEMON_WAIT = "15s"
MOUNT_HEALTH_TIMEOUT_SECONDS = 10.0
MOUNT_HEALTH_POLL_INTERVAL_SECONDS = 0.25


def _check_rclone_installed() -> bool:
    try:
        result = subprocess.run(
            ["rclone", "version"],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except FileNotFoundError:
        return False


def _pick_local_port(requested_port: int = 0) -> int:
    """Return an explicit port or reserve a best-effort ephemeral localhost port."""
    if requested_port > 0:
        return requested_port

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _find_connected_bridge(
    tunnel_config: TunnelConfig,
    bridge_name: str | None = None,
    timeout: int = 10,
) -> BridgeProfile | None:
    if bridge_name:
        bridge = tunnel_config.get_bridge(bridge_name)
        if bridge and _test_ssh_connection(bridge, tunnel_config, timeout=timeout):
            return bridge
        return None

    default = tunnel_config.get_bridge()
    if default and _test_ssh_connection(default, tunnel_config, timeout=timeout):
        return default

    for bridge in tunnel_config.list_bridges():
        if _test_ssh_connection(bridge, tunnel_config, timeout=timeout):
            return bridge

    return None


def _get_identity_file(bridge: BridgeProfile) -> Path:
    if bridge.identity_file:
        return Path(bridge.identity_file).expanduser()
    for name in ("id_ed25519", "id_rsa"):
        p = Path.home() / ".ssh" / name
        if p.exists():
            return p
    raise FileNotFoundError("No SSH identity file found")


def _establish_tunnel(bridge: BridgeProfile, tunnel_config: TunnelConfig, local_port: int) -> bool:
    ssh_host_alias = None
    ssh_config_path = Path.home() / ".ssh" / "config"
    if ssh_config_path.exists():
        content = ssh_config_path.read_text()
        if re.search(rf"^Host\s+{re.escape(bridge.name)}\s", content, re.MULTILINE):
            ssh_host_alias = bridge.name

    if ssh_host_alias:
        ssh_cmd = [
            "ssh",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-fN",
            "-L",
            f"{local_port}:localhost:{bridge.ssh_port}",
            ssh_host_alias,
        ]
    else:
        proxy_cmd = _get_proxy_command(bridge, tunnel_config.rtunnel_bin)
        ssh_cmd = [
            "ssh",
            "-o",
            f"ProxyCommand={proxy_cmd}",
            "-o",
            "StrictHostKeyChecking=no",
            "-o",
            "UserKnownHostsFile=/dev/null",
            "-o",
            "LogLevel=ERROR",
            "-o",
            "ExitOnForwardFailure=yes",
            "-o",
            "ServerAliveInterval=30",
            "-o",
            "ServerAliveCountMax=3",
            "-fN",
            "-L",
            f"{local_port}:localhost:{bridge.ssh_port}",
            f"{bridge.ssh_user}@localhost",
        ]

    try:
        existing_pid = _find_tunnel_pid(local_port)
        if existing_pid:
            return True

        result = subprocess.run(
            ssh_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        )
        if result.returncode != 0:
            return False

        for _ in range(50):
            if _find_tunnel_pid(local_port):
                return True
            time.sleep(0.2)

        return False
    except Exception:
        return False


def _find_tunnel_pid(local_port: int) -> int | None:
    try:
        result = subprocess.run(
            ["ss", "-tlnp"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.splitlines():
            if f":{local_port}" in line and "127.0.0.1" in line:
                match = re.search(r"pid=(\d+)", line)
                if match:
                    return int(match.group(1))
    except Exception:
        pass
    return None


def _kill_tunnel(local_port: int) -> bool:
    if local_port <= 0:
        return True
    pid = _find_tunnel_pid(local_port)
    if not pid:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        try:
            os.kill(pid, 0)
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        return True
    except Exception:
        return False


def _ensure_sftp_server(
    bridge: BridgeProfile, tunnel_config: TunnelConfig, local_port: int, ctx: Context | None = None
) -> bool:
    identity = _get_identity_file(bridge)
    ssh_opts = [
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "LogLevel=ERROR",
        "-o",
        f"IdentityFile={identity}",
        "-p",
        str(local_port),
        f"{bridge.ssh_user}@localhost",
    ]

    check_cmd = ["ssh", *ssh_opts, "ls", "/usr/lib/openssh/sftp-server"]
    try:
        result = subprocess.run(check_cmd, capture_output=True, text=True, timeout=10)
        if result.returncode == 0:
            return True
    except Exception:
        pass

    if ctx is not None:
        emit_info(ctx, "Installing SFTP server on remote notebook...")
    else:
        click.echo("Installing SFTP server on remote notebook...", err=True)

    install_script = (
        "apt-get download openssh-sftp-server >/dev/null 2>&1 && "
        "dpkg-deb -x openssh-sftp-server*.deb /tmp/sftp-extract >/dev/null 2>&1 && "
        "cp /tmp/sftp-extract/usr/lib/openssh/sftp-server /usr/lib/openssh/sftp-server && "
        "ln -sf /usr/lib/openssh/sftp-server /usr/lib/sftp-server 2>/dev/null; "
        "echo done"
    )

    install_cmd = ["ssh", *ssh_opts, install_script]
    try:
        result = subprocess.run(install_cmd, capture_output=True, text=True, timeout=60)
        if result.returncode == 0 and "done" in (result.stdout or ""):
            return True
        err = (result.stderr or "").strip()
        if err:
            click.echo(f"SFTP install failed: {err}", err=True)
        return False
    except Exception as e:
        click.echo(f"SFTP install failed: {e}", err=True)
        return False


def _get_remote_path(config: Config) -> str:
    if config.target_dir:
        return config.target_dir
    if config.username:
        return f"/inspire/hdd/global_user/{config.username}"
    raise ConfigError(
        "Cannot determine remote path.\n"
        "Set defaults.target_dir:\n"
        "  inspire config set defaults.target_dir /inspire/hdd/..."
    )


def _cleanup_failed_mount_start(
    *,
    mount_point: Path,
    local_port: int,
    created_tunnel: bool,
    rclone_pid: int | None = None,
) -> None:
    _kill_process(rclone_pid)
    if created_tunnel:
        _kill_tunnel(local_port)
    if not _is_mounted(mount_point):
        _remove_mount_state(mount_point)


def _release_mount_state(mount_point: Path, state: dict | None) -> None:
    if state:
        _kill_process(int(state.get("rclone_pid", 0) or 0))
        _kill_tunnel(int(state.get("local_port", 0) or 0))
    else:
        for pid in _find_rclone_mount_pids(mount_point):
            _kill_process(pid)
    _remove_mount_state(mount_point)


def _is_mounted(mount_point: Path) -> bool:
    try:
        result = subprocess.run(
            ["mountpoint", "-q", str(mount_point)],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except Exception:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            check=False,
        )
        return str(mount_point) in result.stdout


def _save_mount_state(
    mount_point: Path,
    local_port: int,
    bridge_name: str,
    tunnel_pid: int | None,
    rclone_pid: int | None,
) -> None:
    MOUNT_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    state = _load_all_mount_states()
    state[str(mount_point)] = {
        "mount_point": str(mount_point),
        "local_port": local_port,
        "bridge_name": bridge_name,
        "tunnel_pid": tunnel_pid,
        "rclone_pid": rclone_pid,
        "started_at": time.time(),
    }
    MOUNT_STATE_FILE.write_text(json.dumps(state, indent=2))


def _remove_mount_state(mount_point: Path) -> None:
    state = _load_all_mount_states()
    state.pop(str(mount_point), None)
    if state:
        MOUNT_STATE_FILE.write_text(json.dumps(state, indent=2))
    else:
        MOUNT_STATE_FILE.unlink(missing_ok=True)


def _load_all_mount_states() -> dict:
    if MOUNT_STATE_FILE.exists():
        try:
            return json.loads(MOUNT_STATE_FILE.read_text())
        except Exception:
            pass
    return {}


def _load_mount_state(mount_point: Path) -> dict | None:
    return _load_all_mount_states().get(str(mount_point))


def _is_process_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _kill_process(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return True
    try:
        os.kill(pid, signal.SIGTERM)
        time.sleep(0.5)
        if _is_process_alive(pid):
            os.kill(pid, signal.SIGKILL)
        return True
    except ProcessLookupError:
        return True
    except Exception:
        return False


def _find_rclone_mount_pids(mount_point: Path) -> list[int]:
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid=,args="],
            capture_output=True,
            text=True,
            check=False,
        )
    except Exception:
        return []

    target = str(mount_point)
    pids: list[int] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 1)
        if len(parts) != 2:
            continue
        pid_str, args = parts
        if target not in args:
            continue
        if not re.search(r"^(?:\S+/)?rclone\s+mount\b", args):
            continue
        try:
            pids.append(int(pid_str))
        except ValueError:
            continue
    return pids


def _find_rclone_mount_pid(mount_point: Path) -> int | None:
    pids = _find_rclone_mount_pids(mount_point)
    return pids[0] if pids else None


def _inspect_mount(
    mount_point: Path,
    *,
    local_port: int | None = None,
    state: dict | None = None,
) -> dict[str, object]:
    mounted = _is_mounted(mount_point)
    accessible = mounted and _probe_mount_access(mount_point)

    tunnel_pid = _find_tunnel_pid(local_port) if local_port else None
    if state:
        tunnel_pid = int(state.get("tunnel_pid", 0) or 0) or tunnel_pid
        rclone_pid = int(state.get("rclone_pid", 0) or 0) or None
    else:
        rclone_pid = _find_rclone_mount_pid(mount_point)

    tunnel_alive = _is_process_alive(tunnel_pid)
    rclone_alive = _is_process_alive(rclone_pid) if state else bool(rclone_pid)

    if mounted and accessible and (not state or (tunnel_alive and rclone_alive)):
        status = "healthy"
        reason = "mounted and accessible"
    elif mounted:
        status = "unhealthy"
        reason = "mounted but helper processes or filesystem access are unhealthy"
    elif state:
        status = "stale"
        reason = "saved state exists but mount is not active"
    else:
        status = "not-mounted"
        reason = "not mounted"

    return {
        "status": status,
        "reason": reason,
        "mounted": mounted,
        "accessible": accessible,
        "tunnel_pid": tunnel_pid,
        "tunnel_alive": tunnel_alive,
        "rclone_pid": rclone_pid,
        "rclone_alive": rclone_alive,
    }


def _probe_mount_access(mount_point: Path) -> bool:
    try:
        with os.scandir(mount_point) as entries:
            next(entries, None)
        return True
    except OSError:
        return False


def _tail_file(path: Path, lines: int = 20) -> str:
    try:
        content = path.read_text()
    except Exception:
        return ""
    return "\n".join(content.splitlines()[-lines:])


def _wait_for_mount_ready(
    mount_point: Path,
    local_port: int,
    timeout: float = MOUNT_HEALTH_TIMEOUT_SECONDS,
) -> tuple[bool, int | None, str]:
    deadline = time.time() + timeout
    last_reason = "mount did not become ready"

    while time.time() < deadline:
        health = _inspect_mount(mount_point, local_port=local_port)
        rclone_pid = health["rclone_pid"]
        if health["tunnel_pid"] and rclone_pid and health["mounted"] and health["accessible"]:
            return True, int(rclone_pid), ""

        reasons: list[str] = []
        if not health["tunnel_pid"]:
            reasons.append("SSH tunnel is not alive")
        if not rclone_pid:
            reasons.append("rclone daemon is not alive")
        if not health["mounted"]:
            reasons.append("mountpoint is not mounted")
        elif not health["accessible"]:
            reasons.append("mountpoint is mounted but not accessible")
        last_reason = "; ".join(reasons) or last_reason
        time.sleep(MOUNT_HEALTH_POLL_INTERVAL_SECONDS)

    return False, _find_rclone_mount_pid(mount_point), last_reason


def _format_mount_start_error(stderr: str, log_tail: str) -> str:
    parts = [part for part in [stderr.strip(), log_tail.strip()] if part]
    if not parts:
        return "unknown rclone error"
    if len(parts) == 1:
        return parts[0]
    return f"{parts[0]}\n\nRecent rclone log:\n{parts[1]}"


def _mount_health_details(mount_point: Path, state: dict | None = None) -> tuple[str, str]:
    health = _inspect_mount(mount_point, state=state)
    return str(health["status"]), str(health["reason"])


def _build_rclone_mount_command(
    *,
    remote: str,
    mount_point: Path,
    ssh_arg: str,
    cache: str,
    startup_log: Path,
    read_only: bool,
    verbose: bool,
) -> list[str]:
    cmd = [
        "rclone",
        "mount",
        f":sftp:{remote}",
        str(mount_point),
        "--sftp-ssh",
        ssh_arg,
        "--vfs-cache-mode",
        cache,
        "--dir-cache-time",
        DEFAULT_DIR_CACHE_TIME,
        "--attr-timeout",
        DEFAULT_ATTR_TIMEOUT,
        "--daemon-wait",
        DEFAULT_DAEMON_WAIT,
        "--log-file",
        str(startup_log),
    ]
    if read_only:
        cmd.append("--read-only")
    if verbose:
        cmd.append("-v")
    cmd.append("--daemon")
    return cmd


def unmount(mount_point: Path) -> bool:
    try:
        result = subprocess.run(
            ["fusermount", "-u", str(mount_point)],
            capture_output=True,
            check=False,
        )
        if result.returncode == 0:
            return True

        result = subprocess.run(
            ["umount", str(mount_point)],
            capture_output=True,
            check=False,
        )
        return result.returncode == 0
    except Exception as e:
        click.echo(f"Failed to unmount: {e}", err=True)
        return False


def list_mounts() -> list[dict[str, str]]:
    mounts = []
    try:
        result = subprocess.run(
            ["mount"],
            capture_output=True,
            text=True,
            check=False,
        )
        for line in result.stdout.split("\n"):
            if "rclone" in line.lower():
                parts = line.split()
                if len(parts) >= 3:
                    mounts.append(
                        {
                            "device": parts[0],
                            "mount_point": parts[2],
                            "type": "rclone",
                        }
                    )
    except Exception:
        pass
    return mounts


@click.group()
def mount():
    """Mount Inspire shared filesystem locally.

    Uses rclone over an SSH tunnel for fast, reliable file access.
    Experimental: intended for saved notebook bridges and shared-path access.

    \b
    Quick start:
        inspire mount start                    # Mount with defaults
        inspire mount start -b mybridge        # Use specific bridge
        inspire mount start --path ~/data      # Custom mount point
        inspire mount stop                      # Unmount default
        inspire mount stop --all               # Unmount all

    \b
    Prerequisites:
        1. A running notebook with a saved bridge:
           inspire notebook ssh <id> --save-as mybridge
        2. rclone installed: https://rclone.org/install/
    """
    pass


@mount.command("start")
@click.option(
    "--path",
    "-p",
    "mount_path",
    help="Local mount point (default: ~/inspire)",
)
@click.option(
    "--bridge",
    "-b",
    help="Bridge profile name (default: auto-detect first connected)",
)
@click.option(
    "--remote-path",
    help="Remote path to mount (default: target_dir from config)",
)
@click.option(
    "--read-only",
    "-ro",
    is_flag=True,
    help="Mount read-only",
)
@click.option(
    "--cache",
    default="writes",
    type=click.Choice(["off", "minimal", "writes", "full"]),
    help="VFS cache mode (default: writes)",
)
@click.option(
    "--port",
    type=int,
    default=0,
    help="Local port for SSH tunnel (default: auto-pick)",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    help="Verbose output",
)
@pass_context
def mount_start(
    ctx: Context,
    mount_path: Optional[str],
    bridge: Optional[str],
    remote_path: Optional[str],
    read_only: bool,
    cache: str,
    port: int,
    verbose: bool,
) -> None:
    """Mount the Inspire filesystem locally."""
    try:
        if not _check_rclone_installed():
            _handle_error(
                ctx,
                "DependencyError",
                "rclone is not installed.",
                EXIT_GENERAL_ERROR,
                hint="Install rclone from https://rclone.org/install/ or run: curl https://rclone.org/install.sh | sudo bash",
            )

        config, _ = Config.from_files_and_env(
            require_credentials=False,
            require_target_dir=False,
        )
        tunnel_config = load_tunnel_config()

        mount_point = Path(mount_path).expanduser() if mount_path else DEFAULT_MOUNT_POINT

        if _is_mounted(mount_point):
            emit_warning(ctx, f"{mount_point} is already mounted")
            return

        if _load_mount_state(mount_point) and not _is_mounted(mount_point):
            _remove_mount_state(mount_point)

        emit_info(ctx, "Looking for connected bridge...")
        br = _find_connected_bridge(tunnel_config, bridge)
        if not br:
            _handle_error(
                ctx,
                "ConfigError",
                "No connected bridge found.",
                EXIT_CONFIG_ERROR,
                hint="Start a notebook and save a bridge first: inspire notebook ssh <notebook-id> --save-as mybridge",
            )

        emit_info(ctx, f"Using bridge: {br.name}")

        local_port = _pick_local_port(port)
        created_tunnel = False

        if not _find_tunnel_pid(local_port):
            emit_info(ctx, f"Establishing SSH tunnel on port {local_port}...")
            if not _establish_tunnel(br, tunnel_config, local_port):
                _handle_error(
                    ctx,
                    "ConnectionError",
                    f"Failed to establish SSH tunnel on port {local_port}.",
                    EXIT_GENERAL_ERROR,
                    hint="Check bridge connectivity and confirm the saved bridge still works with 'inspire notebook ssh --list'.",
                )
            created_tunnel = True

        emit_info(ctx, "Checking SFTP subsystem...")
        if not _ensure_sftp_server(br, tunnel_config, local_port, ctx):
            _cleanup_failed_mount_start(
                mount_point=mount_point,
                local_port=local_port,
                created_tunnel=created_tunnel,
            )
            _handle_error(
                ctx,
                "ConnectionError",
                "Could not set up SFTP on the remote notebook.",
                EXIT_GENERAL_ERROR,
            )

        remote = remote_path or _get_remote_path(config)
        mount_point.mkdir(parents=True, exist_ok=True)

        identity = _get_identity_file(br)
        ssh_arg = (
            f"ssh -o StrictHostKeyChecking=no "
            f"-o UserKnownHostsFile=/dev/null "
            f"-o LogLevel=ERROR "
            f"-i {identity} -p {local_port} {br.ssh_user}@localhost"
        )
        startup_log = Path(
            tempfile.NamedTemporaryFile(prefix="inspire-mount-", suffix=".log", delete=False).name
        )

        rclone_cmd = _build_rclone_mount_command(
            remote=remote,
            mount_point=mount_point,
            ssh_arg=ssh_arg,
            cache=cache,
            startup_log=startup_log,
            read_only=read_only,
            verbose=verbose,
        )

        if verbose:
            emit_info(ctx, f"Running: {' '.join(shlex.quote(a) for a in rclone_cmd)}")

        result = subprocess.run(
            rclone_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        if result.returncode != 0:
            error_msg = _format_mount_start_error("", _tail_file(startup_log))
            if "allow_other" in error_msg:
                error_msg += (
                    "\n\nFix: uncomment 'user_allow_other' in /etc/fuse.conf "
                    "(requires sudo) or remove --allow-other."
                )
            _cleanup_failed_mount_start(
                mount_point=mount_point,
                local_port=local_port,
                created_tunnel=created_tunnel,
            )
            startup_log.unlink(missing_ok=True)
            _handle_error(
                ctx,
                "MountError",
                f"Mount failed: {error_msg}",
                EXIT_GENERAL_ERROR,
            )

        tunnel_pid = _find_tunnel_pid(local_port)
        ready, rclone_pid, reason = _wait_for_mount_ready(mount_point, local_port)
        if not ready:
            log_tail = _tail_file(startup_log)
            _cleanup_failed_mount_start(
                mount_point=mount_point,
                local_port=local_port,
                created_tunnel=created_tunnel,
                rclone_pid=rclone_pid,
            )
            startup_log.unlink(missing_ok=True)
            _handle_error(
                ctx,
                "MountError",
                f"Mount failed: {_format_mount_start_error(reason, log_tail)}",
                EXIT_GENERAL_ERROR,
            )

        _save_mount_state(mount_point, local_port, br.name, tunnel_pid, rclone_pid)
        startup_log.unlink(missing_ok=True)

        emit_success(
            ctx,
            text=f"Mounted at {mount_point}",
            payload={
                "mount_point": str(mount_point),
                "remote": remote,
                "bridge": br.name,
                "tunnel_port": local_port,
                "rclone_pid": rclone_pid,
            },
        )
        click.echo()
        click.echo(f"  Remote: {remote}")
        click.echo(f"  Bridge: {br.name}")
        click.echo(f"  Tunnel: localhost:{local_port}")
        click.echo()
        click.echo(f"To unmount: inspire mount stop {mount_point}")

    except ConfigError as e:
        _handle_error(ctx, "ConfigError", str(e), EXIT_CONFIG_ERROR)
    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


@mount.command("stop")
@click.argument("mount_point", required=False)
@click.option("--all", "-a", "stop_all", is_flag=True, help="Unmount all")
@pass_context
def mount_stop(ctx: Context, mount_point: Optional[str], stop_all: bool) -> None:
    """Unmount the Inspire filesystem."""
    try:
        if stop_all:
            states = _load_all_mount_states()
            if not states:
                emit_info(ctx, "No mounts recorded")
                return
            for mp_str, info in list(states.items()):
                mp = Path(mp_str)
                emit_info(ctx, f"Unmounting {mp}...")
                if unmount(mp):
                    _release_mount_state(mp, info)
                    emit_success(ctx, text=f"Unmounted {mp}", payload={"mount_point": str(mp)})
                else:
                    _release_mount_state(mp, info)
                    emit_warning(ctx, f"Unmount command failed for {mp}, cleaned up saved state")
            return

        if not mount_point:
            mp = DEFAULT_MOUNT_POINT
            if not _is_mounted(mp) and not _load_mount_state(mp):
                _handle_error(
                    ctx,
                    "ConfigError",
                    "No mount point specified.",
                    EXIT_CONFIG_ERROR,
                    hint="Use 'inspire mount stop <path>' or 'inspire mount stop --all'.",
                )
        else:
            mp = Path(mount_point).expanduser()

        state = _load_mount_state(mp)
        emit_info(ctx, f"Unmounting {mp}...")

        if unmount(mp):
            _release_mount_state(mp, state)
            emit_success(ctx, text=f"Unmounted {mp}", payload={"mount_point": str(mp)})
        else:
            _release_mount_state(mp, state)
            emit_warning(ctx, "Unmount command failed, cleaned up state")

    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


@mount.command("list")
@pass_context
def mount_list(ctx: Context) -> None:
    """List mounted Inspire filesystems."""
    try:
        mounts = list_mounts()
        states = _load_all_mount_states()

        if not mounts:
            if not states:
                emit_info(ctx, "No Inspire filesystems mounted")
                click.echo()
                click.echo("To mount: inspire mount start")
                return
            click.echo(click.style("Managed Mounts (may be stale):", bold=True))
            for mp_str, info in states.items():
                status, _ = _mount_health_details(Path(mp_str), info)
                click.echo(f"  {mp_str}  bridge={info.get('bridge_name', '?')}  [{status}]")
            return

        click.echo(click.style("Mounted Inspire Filesystems:", bold=True))
        click.echo()
        for i, m in enumerate(mounts, 1):
            state = states.get(m["mount_point"])
            status, _ = _mount_health_details(Path(m["mount_point"]), state)
            click.echo(f"{i}. {m['device']}")
            click.echo(f"   Mount point: {m['mount_point']}  [{status}]")
            if state:
                click.echo(f"   Bridge: {state.get('bridge_name', '?')}")
            click.echo()

        click.echo("To unmount: inspire mount stop <path>")

    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)


@mount.command("status")
@click.argument("mount_point", required=False)
@pass_context
def mount_status(ctx: Context, mount_point: Optional[str]) -> None:
    """Check mount status."""
    try:
        if mount_point:
            mp = Path(mount_point).expanduser()
            state = _load_mount_state(mp)
            status, reason = _mount_health_details(mp, state)
            details = ""
            if state:
                details = (
                    f"  bridge={state.get('bridge_name', '?')}"
                    f"  port={state.get('local_port', '?')}"
                )
            if status == "healthy":
                emit_success(
                    ctx, text=f"{mp} is mounted{details}", payload={"mount_point": str(mp)}
                )
            elif status == "unhealthy":
                emit_warning(ctx, f"{mp} is mounted but unhealthy{details}: {reason}")
            elif status == "stale":
                emit_warning(ctx, f"{mp} is stale{details}: {reason}")
            else:
                emit_info(ctx, f"{mp} is not mounted")
        else:
            mounts = list_mounts()
            states = _load_all_mount_states()
            if mounts:
                emit_info(ctx, f"{len(mounts)} filesystem(s) mounted:")
                for m in mounts:
                    state = states.get(m["mount_point"])
                    status, _ = _mount_health_details(Path(m["mount_point"]), state)
                    click.echo(f"  {m['mount_point']}  [{status}]")
            elif states:
                emit_info(ctx, f"{len(states)} managed mount(s) in state file")
            else:
                emit_info(ctx, "No filesystems mounted")

    except Exception as e:
        _handle_error(ctx, "Error", str(e), EXIT_GENERAL_ERROR)
