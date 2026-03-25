---
name: inspire
description: Use when the user asks to interact with the Inspire HPC platform - submitting GPU jobs, syncing code, managing notebooks, monitoring status, and executing commands on the Bridge runner. Also use when the user asks about inspire-cli commands, workflows, or configuration.
allowed-tools: Bash(inspire *), Bash(uv run inspire *), Bash(ssh *)
---

# Inspire CLI

CLI for the Inspire HPC training platform. Run `inspire --help` and `inspire <command> --help` for flags and syntax — don't memorize them, they change often.

## What it can do

- **Setup**: `init --discover` (auto-detect projects/workspaces/compute groups via browser login)
- **Jobs**: `job create`, `job status/logs/list`, `job stop/wait`, `run` (quick submit with auto-sync)
- **HPC**: `hpc create`, `hpc status/list`, `hpc stop/wait`, `hpc script`
- **Notebooks**: `notebook create/list/start/stop`, `notebook ssh` (tunnel + shell), `notebook top` (GPU monitoring)
- **Code sync**: `sync` (rsync local cwd to shared filesystem via SSH tunnel), `bridge exec` (run remote commands)
- **File transfer**: `bridge scp` (upload/download via tunnel)
- **Tunnels**: `tunnel add/list/status`, `notebook ssh --alias` (create reusable bridge profiles)
- **Info**: `resources list/nodes`, `project list`, `image list`, `config show/check`

## Platform knowledge (things you can't get from --help)

### Network topology
- **CPU / 4090 notebooks** → have internet. SSH auto-installs everything. Zero setup.
- **H100 / H200 notebooks** → NO internet. SSH needs pre-placed `rtunnel_bin` on shared filesystem, plus either `apt_mirror_url` or pre-placed `dropbear_deb_dir` for the SSH server.
- **Jobs** → NO internet. Same GPU clusters. All dependencies must be pre-installed in the image or on the shared filesystem.
- **HPC jobs** → CPU/slurm workloads. They should use CPU/HPC partitions and slurm-enabled images only.

### Shared filesystem
- `/inspire/hdd/global_user/<username>` — visible from ALL notebooks across ALL projects. Put SSH tools here.
- `/inspire/hdd/project/<project-slug>/<username>` — project-specific workdir. This is `target_dir` for code sync.
- These paths come from the API catalog (`init --discover`) but **must be verified via CPU notebook SSH** — the catalog can be wrong.

### Project auto-selection
- When no `--project` flag is given, auto-selection picks from available projects.
- Sort order: `project_order` (user preference) > `gpu_unlimited` (tiebreaker) > `priority` > name.
- `project_order` is set in `.inspire/config.toml` under `[defaults]` — list of project **names**.
- "Unlimited GPU hours" does NOT mean unlimited concurrent GPUs. A project can have no hour cap but still be full.
- Notebook compute-group auto-selection is workspace-scoped. Evaluate availability inside the selected workspace, not across unrelated workspaces.

### Presentation and lookup behavior
- Human-readable list-style commands prefer Rich tables when Rich is installed; plain-text fallback still exists.
- `notebook list` shows separate CPU / GPU / Memory columns.
- `notebook status` normalizes `created_at` from ISO strings or Unix timestamps (including millisecond timestamps) into a UTC+8 human display while preserving the raw value.
- `image detail` is workspace-aware and can resolve full IDs, partial IDs, names, or URLs; ambiguous matches may trigger an interactive choice.
- `image list --source personal-visible` matches the web UI's personal visible source tab.
- `resources list` emphasizes grouped, human-readable summaries and schedulable resource-spec visibility.

### SSH tunnel architecture
- `notebook ssh` opens a Jupyter terminal via WebSocket, runs a setup script (installs SSH server + rtunnel), then connects via the platform's HTTP proxy.
- First SSH to a fresh notebook takes 10-60s (setup). Subsequent connections reuse the running processes.
- `--alias <name>` adds a reusable bridge alias. After that, `ssh <name>`, `bridge exec`, `sync`, `bridge scp` all work through it.
- Tunnels break when notebooks restart. `bridge exec/ssh` auto-reconnect for notebook-backed profiles.

## Rules the model should always follow

1. **Sync uses current directory** — `inspire sync` rsyncs the current local directory to the saved remote target and preserves remote-only files. It does not read `INSPIRE_SYNC_BRIDGE` or `INSPIRE_TARGET_DIR` from the environment.
2. **Jobs start in an unknown directory** — Always `cd $TARGET_DIR && ...` in job commands.
3. **Use `.` not `source`** for venv activation in job commands (POSIX compatibility).
4. **GPU nodes have NO internet** — pip install, git clone, curl all fail. Use a CPU notebook/bridge.
5. **Priority range is 1-10** — Outside this range causes API errors.
6. **Python output buffering** — `PYTHONUNBUFFERED=1` or `print(..., flush=True)` for live log tailing.
7. **Always run `--help`** before guessing flags — the CLI iterates fast and flags change.
8. **HPC is preset-driven** — prefer `inspire hpc create --preset ...` with config-backed `spec_id` / compute group values instead of inventing ad-hoc CPU scheduling parameters.
9. **HPC images must include slurm** — use images such as `slurm-gromacs:*` or other slurm-capable images; generic CPU images are not enough.
10. **HPC entrypoints are sbatch scripts** — keep the required `#SBATCH` headers and ensure the final workload launch uses `srun`.
