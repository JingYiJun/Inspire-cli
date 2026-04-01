"""Utilities for building and validating HPC execution snippets."""

from __future__ import annotations


def _shell_quote(text: str) -> str:
    return text.replace("'", "'\\''")


def build_srun_command(command: str) -> str:
    """Wrap a plain command in the required `srun` launcher."""
    stripped = command.strip()
    if "srun" in stripped:
        return stripped
    return f"srun bash -lc '{_shell_quote(stripped)}'"


def extract_hpc_execution_body(script: str) -> str:
    """Strip sbatch headers and return the execution body submitted to the backend."""
    lines = []
    for raw_line in str(script).splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        if stripped.startswith("#!"):
            continue
        if stripped.startswith("#SBATCH"):
            continue
        if stripped.startswith("##"):
            continue
        lines.append(raw_line.rstrip())

    body = "\n".join(lines).strip()
    if not body:
        raise ValueError("HPC script must include an srun command")
    return body


def validate_hpc_script(script: str) -> None:
    """Validate that a script contains the mandatory launcher."""
    if "srun" not in extract_hpc_execution_body(script):
        raise ValueError("HPC script must include an srun command")


def build_hpc_sbatch_script(
    *,
    command: str,
    number_of_tasks: int,
    cpus_per_task: int,
    memory_per_cpu: str,
    time_limit: str,
    extra_sbatch_lines: list[str] | None = None,
) -> str:
    """Build the execution body for an HPC job; sbatch headers are backend-owned."""
    _ = number_of_tasks
    _ = cpus_per_task
    _ = memory_per_cpu
    _ = time_limit
    _ = extra_sbatch_lines
    script = build_srun_command(command)
    validate_hpc_script(script)
    return script


__all__ = [
    "build_hpc_sbatch_script",
    "build_srun_command",
    "extract_hpc_execution_body",
    "validate_hpc_script",
]
