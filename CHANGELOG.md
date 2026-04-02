# Changelog

## Unreleased

### Breaking Changes

- Removed deprecated `inspire bridge exec --no-tunnel` flag. SSH tunnel is now the only execution path for command execution.
- Removed deprecated `inspire sync --via-action` and `--transport` flags. `inspire sync` now uses the SSH tunnel path only.

### Features

- **Unified error handling** across all tunnel commands for consistent JSON/human formatting
- **Added `--json` flag to all commands** for consistent scripting/automation support
- **Notebook list enhancements:**
  - Customizable columns via `--columns` / `-c` flag
  - Tunnel priority sorting (notebooks with tunnels appear first)
  - New `--tunneled` flag to show only notebooks with SSH tunnels
  - Removed notebook ID from default view (use `-c name,status,resource,id` to show)

### Fixed

- Fixed module naming conflicts that prevented non-editable wheel installation
- Fixed notebook ID format normalization for tunnel matching

## v0.2.4 (2025-01-01)

### Features

- Job management commands (create, status, logs, list, stop, wait)
- Notebook management commands (list, create, start, stop, ssh)
- Resource availability listing (GPUs, nodes)
- Quick job submission with auto-resource selection (`run`)
- Code sync to Bridge runner (`sync`)
- Bridge remote execution (`bridge exec`, `bridge ssh`)
- SSH tunnel management (add, remove, status, list, ssh-config)
- Configuration management (show, check, env) with TOML + env var loading
- Project initialization with environment detection
- SSH tunnel execution for bridge commands
- Human-readable and JSON output formatting
- Remote environment variable injection via `[remote_env]` config
