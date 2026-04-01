# Inspire CLI

Command-line interface for the Inspire HPC training platform.

## Installation

```bash
# Via SSH (recommended)
uv tool install git+ssh://git@github.com/EmbodiedForge/Inspire-cli.git

# Or via HTTPS
uv tool install git+https://github.com/EmbodiedForge/Inspire-cli.git
```

### Local Development

```bash
uv tool install -e .
inspire --help
```

## Quick Start

### 1. Auto-discover your platform

```bash
inspire init --discover -u YOUR_USERNAME --base-url https://your-platform.com
```

This opens a browser to log in, then automatically discovers your projects, workspaces, compute groups, and shared filesystem paths. Writes both global (`~/.config/inspire/config.toml`) and project (`.inspire/config.toml`) configs.

Set your password as an env var to avoid repeated prompts:
```bash
export INSPIRE_PASSWORD="your_password"
```

### 2. Verify

```bash
inspire config show    # Check all values resolved
inspire config check   # Validate API auth
```

### 3. Start using

```bash
inspire resources list          # View GPU availability
inspire notebook create --name dev --resource 4xCPU --wait
inspire notebook ssh <id>       # SSH into notebook (auto-installs tunnel)
inspire hpc create --name cpu-demo --preset cpu-small --command "python main.py"
```

## Commands

| Command | Description |
|---------|-------------|
| `inspire job create` | Submit a training job |
| `inspire job status/logs/list` | Monitor and manage jobs |
| `inspire job stop/wait` | Stop or wait for a job |
| `inspire hpc create/status/list` | Create HPC jobs and list/status your current-account HPC tasks |
| `inspire hpc stop/wait/script` | Stop, wait for, or inspect cached sbatch scripts |
| `inspire run "<cmd>"` | Quick job with auto resource selection |
| `inspire sync` | Rsync local code to shared filesystem (via SSH tunnel) |
| `inspire bridge exec "<cmd>"` | Run command on a Bridge profile via SSH tunnel |
| `inspire bridge ssh [--bridge <name>]` | Interactive SSH shell to a Bridge profile |
| `inspire bridge scp <source> <destination>` | Upload/download files via Bridge tunnel |
| `inspire notebook list/create/status` | List, create, or inspect notebook instances |
| `inspire notebook start/stop` | Start or stop a notebook |
| `inspire notebook ssh <id>` | SSH into notebook (sets up tunnel) |
| `inspire notebook top` | Show GPU utilization/memory for tunnel-backed notebooks |
| `inspire image list/detail` | Browse Docker images |
| `inspire image save/register` | Save or register custom images |
| `inspire tunnel add/list/status` | Manage SSH tunnels to Bridge |
| `inspire tunnel ssh-config` | Generate SSH config for direct access |
| `inspire project list` | View projects and GPU quota |
| `inspire resources list/nodes` | View GPU availability |
| `inspire config show/check` | Inspect and validate configuration |
| `inspire init` | Generate starter config from env vars |
| `inspire init --discover` | Auto-discover projects, workspaces, compute groups |

## Examples

```bash
# Submit a training job
inspire job create --name "train-v1" --resource "4xH200" --command "bash train.sh"

# Submit an HPC (CPU/slurm) job from a preset
inspire hpc create --name "gromacs-demo" --preset cpu-small --command "python main.py"

# Use a full sbatch script from file
inspire hpc create --name "ffmpeg-batch" --preset cpu-small --script-file ./job.sbatch

# List my HPC jobs from the remote web UI
inspire hpc list

# List my HPC jobs from both cpu + hpc workspaces
inspire hpc list --all -n 100

# List only jobs in specific statuses
inspire hpc list -s RUNNING -s STOPPED

# Revisit the old local submission cache only
inspire hpc list --cache

# Quick run with auto-selected resources, sync code and follow logs
inspire run "python train.py --epochs 100" --sync --watch

# Sync local directory and verify
inspire sync && inspire bridge exec "ls -la"

# Set up SSH tunnel to a notebook
inspire notebook ssh <notebook-id> --alias mybridge
ssh mybridge

# Check live GPU usage for all saved notebook tunnels
inspire notebook top
inspire notebook top --bridge mybridge --watch

# Browse workspace-aware image sources
inspire image list --source personal-visible
inspire image detail my-image --workspace gpu

# Copy files through a configured bridge profile
inspire bridge scp ./model.py /tmp/model.py --bridge mybridge
inspire bridge scp -d /tmp/checkpoints/ ./checkpoints/ -r --bridge mybridge

# Check GPU availability and project quota
inspire resources list
inspire project list
```

## Recent CLI Notes

- Human-readable list commands prefer Rich tables when Rich is available and fall back to plain text otherwise.
- `inspire notebook list` now shows separate CPU, GPU, and Memory columns instead of one packed resource string.
- `inspire notebook status` renders a more readable detail view and normalizes `created_at` from ISO strings or Unix timestamps (including milliseconds) into a UTC+8 display while keeping the raw value.
- Notebook compute-group auto-selection is workspace-scoped. Availability is evaluated inside the selected workspace, not across unrelated workspaces.
- `inspire image detail` is workspace-aware and can resolve a full ID, partial ID, image name, or full URL. When multiple workspaces match, the CLI may ask you to choose.
- `inspire image list --source personal-visible` matches the web UI's personal visible tab.
- `inspire resources list` now emphasizes grouped human-readable summaries and schedulable resource-spec visibility.

## HPC Notes

- `inspire hpc` is intended for CPU/slurm-style workloads, not regular GPU training jobs.
- Submit HPC jobs to CPU/HPC workspaces or partitions, preferably via config-backed presets.
- HPC images must include slurm (for example `slurm-gromacs:*` or another slurm-enabled image).
- `inspire hpc list` now defaults to the remote web list and only shows jobs created by the current account.
- `inspire hpc list --all` combines the current account's jobs from both `cpu` and `hpc` workspaces, with a default limit of `100` per workspace.
- `inspire hpc list --cache` keeps the old local-cache behavior if you only want locally submitted entries.
- The HPC `entrypoint` is an sbatch script body. The platform backend owns the `#!/bin/bash` and `#SBATCH ...` headers; CLI submission only needs the execution body, and the workload must launch with `srun`.

## SSH/SCP Reliability Notes

- There is no `inspire tunnel start` command. Create or refresh bridge profiles with `inspire notebook ssh <notebook-id> --alias <name>` (or `inspire tunnel add` / `inspire tunnel update`), then validate with `inspire tunnel status`.
- `inspire bridge ssh` and `inspire bridge scp` validate `--bridge` names before connectivity checks. If a profile is missing, run `inspire tunnel list`.
- Saved notebook profiles now store the source notebook ID. Reusing the same alias for a different notebook refreshes the tunnel instead of reusing stale tunnel state.
- `inspire bridge ssh`, `inspire bridge exec`, and interactive `inspire notebook ssh` auto-rebuild/reconnect dropped tunnels for notebook-backed profiles, using `tunnel.retries` / `tunnel.retry_pause` as retry controls.
- Non-notebook tunnel profiles (for example, manually added profiles without `notebook_id`) cannot be auto-rebuilt and still require manual tunnel recovery.
- `inspire tunnel ssh-config` now writes shell-quoted `ProxyCommand` entries so proxy URLs with query parameters/tokens remain safe in `~/.ssh/config`.

## Configuration

The recommended way to configure is `inspire init --discover`, which auto-detects projects, workspaces, compute groups, and writes config files.

Config files are loaded in order (later overrides earlier):
1. Global: `~/.config/inspire/config.toml`
2. Project: `./.inspire/config.toml`
3. Environment variables

Account password lookup follows the same layered model:
1. `[accounts."<username>"].password` from global config
2. `[accounts."<username>"].password` from project config (overrides global for same username)
3. `INSPIRE_PASSWORD` (fallback only if no account password was found)

Legacy `[auth].password` is still supported, but account passwords take precedence when both are present.

Run `inspire init --discover` to auto-configure, or `inspire config show` to inspect the merged result.

`inspire sync` uses `rsync` over the SSH tunnel. It always syncs the current working
directory. On the first run, provide the bridge and remote target directory:

```bash
cd ~/path/to/your/project
inspire sync your-bridge /inspire/.../path/to/your/project
```

This persists `bridge` and `target_dir` to `./.inspire/config.toml`,
so later `inspire sync` runs can omit them.

`inspire sync` always preserves remote-only files. Local files with the same path
still overwrite remote files of the same name. The preserved `.inspire/` directory
is still excluded from sync.

`inspire init` probe-only options are effective only with `--discover --probe-shared-path`:
`--probe-limit`, `--probe-keep-notebooks`, `--probe-pubkey`/`--pubkey`, and `--probe-timeout`.
Without that combination, they are accepted but ignored.

Example `config.toml`:

```toml
[auth]
username = "your_username"

[accounts."your_username"]
# Optional: supports multi-account setups in global and/or project config
password = "your_password"

[api]
base_url = "https://your-inspire-platform.com"

[bridge]
# Timeout in seconds for `inspire bridge exec`
action_timeout = 600

[workspaces]
# cpu = "ws-..."       # Default workspace (CPU jobs / notebooks)
# gpu = "ws-..."       # GPU workspace (H100/H200 jobs)
# internet = "ws-..."  # Internet-enabled GPU workspace (e.g. RTX 4090)
# hpc = "ws-..."       # HPC workspace for slurm CPU jobs
# special = "ws-..."   # Custom alias (use with --workspace special)

[hpc]
# image = "docker.sii.shaipower.online/inspire-studio/slurm-gromacs:latest"
# image_type = "SOURCE_PUBLIC"
# priority = 4
# ttl_after_finish_seconds = 600
# default_preset = "cpu-small"

[hpc.presets.cpu-small]
# workspace = "hpc"
# logic_compute_group_id = "lcg-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
# spec_id = "quota-xxxxxxxx"
# number_of_tasks = 1
# cpus_per_task = 4
# memory_per_cpu = "4G"
# time = "0-12:00:00"
# enable_hyper_threading = false

[[compute_groups]]
name = "H100 Cluster"
id = "lcg-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
gpu_type = "H100"

[ssh]
# For GPU notebooks (H100/H200) without internet:
# rtunnel_bin = "/inspire/shared/tools/rtunnel"
# Option A: APT mirror (simpler — no pre-placed debs needed)
# apt_mirror_url = "http://nexus.example.com/repository/ubuntu/"
# Option B: Pre-placed dropbear debs
# dropbear_deb_dir = "/inspire/shared/debs/dropbear"
```

View current config:
```bash
inspire config show
inspire config show --json
inspire config check   # Validate config + API auth
inspire --json config check
inspire config check --json
inspire init --json --template --project --force
```

## Environment Variables

| Variable | Description |
|----------|-------------|
| `INSPIRE_USERNAME` | Platform username |
| `INSPIRE_PASSWORD` | Platform password |
| `INSPIRE_BASE_URL` | API base URL |
| `INSPIRE_TARGET_DIR` | Shared filesystem path |
| `INSPIRE_WORKSPACE_ID` | Default workspace ID |
| `INSPIRE_WORKSPACE_CPU_ID` | CPU workspace ID (default workspace) |
| `INSPIRE_WORKSPACE_GPU_ID` | GPU workspace ID (H100/H200) |
| `INSPIRE_WORKSPACE_INTERNET_ID` | Internet-enabled workspace ID (e.g. RTX 4090) |
| `INSPIRE_WORKSPACE_HPC_ID` | HPC workspace ID for slurm CPU jobs |
| `INSPIRE_PROJECT_ID` | Default project ID |
| `INSP_IMAGE` | Default Docker image |
| `INSP_PRIORITY` | Job priority (1-10) |
| `INSPIRE_HPC_IMAGE` | Default HPC image (must include slurm) |
| `INSPIRE_HPC_IMAGE_TYPE` | Default HPC image source type |
| `INSPIRE_HPC_PRIORITY` | Default HPC priority |
| `INSPIRE_HPC_TTL_AFTER_FINISH_SECONDS` | Default HPC post-finish retention time |
| `INSPIRE_HPC_DEFAULT_PRESET` | Default HPC preset name |

Note: `inspire sync` does not read `INSPIRE_TARGET_DIR` from the environment.
For sync, pass `bridge` and `target_dir` on the first run, then reuse the
saved values in `./.inspire/config.toml`.
