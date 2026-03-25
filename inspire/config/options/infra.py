"""Config options: SSH, Tunnel, Bridge, and Paths."""

from __future__ import annotations

from inspire.config.rtunnel_defaults import default_rtunnel_download_url
from inspire.config.schema_models import (
    ConfigOption,
    _parse_float,
    _parse_int,
)

SSH_OPTIONS: list[ConfigOption] = [
    ConfigOption(
        env_var="INSPIRE_RTUNNEL_BIN",
        toml_key="ssh.rtunnel_bin",
        field_name="rtunnel_bin",
        description="Path to rtunnel binary",
        default=None,
        category="SSH",
        scope="global",
    ),
    ConfigOption(
        env_var="INSPIRE_SSHD_DEB_DIR",
        toml_key="ssh.sshd_deb_dir",
        field_name="sshd_deb_dir",
        description="Directory containing sshd deb package",
        default=None,
        category="SSH",
        scope="global",
    ),
    ConfigOption(
        env_var="INSPIRE_DROPBEAR_DEB_DIR",
        toml_key="ssh.dropbear_deb_dir",
        field_name="dropbear_deb_dir",
        description="Directory containing dropbear deb package",
        default=None,
        category="SSH",
        scope="global",
    ),
    ConfigOption(
        env_var="INSPIRE_SETUP_SCRIPT",
        toml_key="ssh.setup_script",
        field_name="setup_script",
        description="Path to SSH setup script on the cluster",
        default=None,
        category="SSH",
        scope="global",
        secret=True,
    ),
    ConfigOption(
        env_var="INSPIRE_RTUNNEL_DOWNLOAD_URL",
        toml_key="ssh.rtunnel_download_url",
        field_name="rtunnel_download_url",
        description="Download URL for rtunnel binary",
        default=default_rtunnel_download_url(),
        category="SSH",
        scope="global",
    ),
    ConfigOption(
        env_var="INSPIRE_APT_MIRROR_URL",
        toml_key="ssh.apt_mirror_url",
        field_name="apt_mirror_url",
        description="APT mirror URL for offline dropbear installation (e.g. http://nexus.example/repository/ubuntu/)",
        default=None,
        category="SSH",
        scope="global",
    ),
]

TUNNEL_OPTIONS: list[ConfigOption] = [
    ConfigOption(
        env_var="INSPIRE_TUNNEL_RETRIES",
        toml_key="tunnel.retries",
        field_name="tunnel_retries",
        description="SSH tunnel connection retries",
        default=3,
        category="Tunnel",
        parser=_parse_int,
        scope="global",
    ),
    ConfigOption(
        env_var="INSPIRE_TUNNEL_RETRY_PAUSE",
        toml_key="tunnel.retry_pause",
        field_name="tunnel_retry_pause",
        description="Seconds to wait between SSH tunnel retries",
        default=2.0,
        category="Tunnel",
        parser=_parse_float,
        scope="global",
    ),
]

BRIDGE_OPTIONS: list[ConfigOption] = [
    ConfigOption(
        env_var="INSPIRE_BRIDGE_ACTION_TIMEOUT",
        toml_key="bridge.action_timeout",
        field_name="bridge_action_timeout",
        description="Bridge exec timeout in seconds",
        default=600,
        category="Bridge",
        parser=_parse_int,
        scope="global",
    ),
]

PATHS_OPTIONS: list[ConfigOption] = [
    ConfigOption(
        env_var="INSPIRE_TARGET_DIR",
        toml_key="paths.target_dir",
        field_name="target_dir",
        description="Target directory on Bridge shared filesystem",
        default=None,
        category="Paths",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_SYNC_SOURCE_DIR",
        toml_key="paths.sync_source_dir",
        field_name="sync_source_dir",
        description="Deprecated: sync now always uses the current working directory",
        default=None,
        category="Paths",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_SYNC_BRIDGE",
        toml_key="paths.sync_bridge",
        field_name="sync_bridge",
        description="Saved bridge profile for rsync-based sync (sync ignores env-sourced values)",
        default=None,
        category="Paths",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_LOG_PATTERN",
        toml_key="paths.log_pattern",
        field_name="log_pattern",
        description="Log file glob pattern",
        default="training_master_*.log",
        category="Paths",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_JOB_CACHE",
        toml_key="paths.job_cache",
        field_name="job_cache_path",
        description="Local job cache file path",
        default="~/.inspire/jobs.json",
        category="Paths",
        scope="global",
    ),
    ConfigOption(
        env_var="INSP_LOG_CACHE_DIR",
        toml_key="paths.log_cache_dir",
        field_name="log_cache_dir",
        description="Cache directory for remote logs",
        default="~/.inspire/logs",
        category="Paths",
        scope="global",
    ),
]
