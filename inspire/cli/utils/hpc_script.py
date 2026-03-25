"""Utilities for building and validating HPC sbatch scripts."""

from __future__ import annotations

import re


def _shell_quote(text: str) -> str:
    return text.replace("'", "'\\''")


def build_srun_command(command: str) -> str:
    """Wrap a plain command in the required `srun` launcher."""
    stripped = command.strip()
    if "srun" in stripped:
        return stripped
    return f"srun bash -lc '{_shell_quote(stripped)}'"


def _parse_memory_gib(memory_per_cpu: str) -> int:
    match = re.fullmatch(r"\s*(\d+)\s*[gG]\s*", str(memory_per_cpu))
    if not match:
        raise ValueError("memory_per_cpu must use GiB syntax like '4G'")
    return int(match.group(1))


def validate_hpc_script(script: str) -> None:
    """Validate that a script contains the mandatory launcher."""
    if "srun" not in script:
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
    """Build a canonical sbatch entrypoint for HPC jobs."""
    mem_gib = _parse_memory_gib(memory_per_cpu)
    total_mem_gib = mem_gib * int(number_of_tasks) * int(cpus_per_task)
    sbatch_lines = [
        "#!/bin/bash",
        "#SBATCH -o /hpc_logs/slurm-%j.out",
        "#SBATCH -e /hpc_logs/slurm-%j.err",
        f"#SBATCH --ntasks={int(number_of_tasks)}",
        f"#SBATCH --cpus-per-task={int(cpus_per_task)}",
        f"#SBATCH --mem={total_mem_gib}G",
        f"#SBATCH --time={time_limit}",
    ]
    for line in extra_sbatch_lines or []:
        text = str(line).strip()
        if text:
            sbatch_lines.append(text)
    script = "\n".join(sbatch_lines + ["", build_srun_command(command), ""])
    validate_hpc_script(script)
    return script


__all__ = ["build_hpc_sbatch_script", "build_srun_command", "validate_hpc_script"]
