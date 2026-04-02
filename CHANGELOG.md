# Changelog

## Unreleased

### Breaking Changes

- Removed deprecated `inspire bridge exec --no-tunnel` flag. SSH tunnel is now the default execution path for command execution; workflow path is selected by artifact options.
- Removed deprecated `inspire sync --via-action` flag. Use `--transport workflow` explicitly when workflow transport is required.

### Features

- Added `inspire config set` for guided and explicit config updates, including interactive menus, validation, and safer handling of user-managed keys.
- Added `inspire project select` for interactive `project_order` selection and persisted project-priority updates.
- Added `inspire resources specs` plus OpenAPI workspace-spec support to inspect and refresh cached workspace hardware specs.
- Added `inspire mount` to mount Inspire shared filesystem locally through an existing bridge profile and SSH tunnel.
- Added CLI completion helpers and documented zsh completion setup in `README.md`.

### Improvements

- Expanded `inspire init --discover` flow and wizard support for better account/workspace/project discovery and setup ergonomics.
- Improved notebook and resources UX, including richer notebook listing controls (`--columns`, `--tunneled`, `--json`) and clearer resource listing behavior.
- Improved tunnel and bridge command reliability, status reporting, and SSH config synchronization.
- Extended config loading and schema handling for account-scoped values, defaults, and runtime resolution behavior.

### Fixes

- Fixed notebook image resolution to scope personal-visible image lookup to the selected workspace during create flow.
- Added regression coverage for workspace-scoped image lookup and expanded tests across config, tunnel, notebook, resources, and openapi flows.

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
- Dual execution paths: SSH tunnel (fast) and Gitea/GitHub Actions (fallback)
- Human-readable and JSON output formatting
- Remote environment variable injection via `[remote_env]` config
