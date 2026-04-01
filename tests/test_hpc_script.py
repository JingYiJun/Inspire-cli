import pytest

from inspire.cli.utils.hpc_script import (
    build_hpc_sbatch_script,
    build_srun_command,
    validate_hpc_script,
)


def test_build_srun_command_wraps_plain_command() -> None:
    assert build_srun_command("python main.py") == "srun bash -lc 'python main.py'"


def test_build_hpc_sbatch_script_includes_required_headers() -> None:
    script = build_hpc_sbatch_script(
        command="python main.py",
        number_of_tasks=2,
        cpus_per_task=8,
        memory_per_cpu="4G",
        time_limit="0-12:00:00",
        extra_sbatch_lines=["#SBATCH --partition=hpc"],
    )

    assert script == "srun bash -lc 'python main.py'"


def test_validate_hpc_script_rejects_script_without_srun() -> None:
    with pytest.raises(ValueError, match="srun"):
        validate_hpc_script("#!/bin/bash\n#SBATCH --time=0-01:00:00\npython main.py\n")


def test_validate_hpc_script_accepts_full_sbatch_and_extracts_execution_body() -> None:
    script = """#!/bin/bash
#SBATCH -o /hpc_logs/slurm-%j.out
#SBATCH -e /hpc_logs/slurm-%j.err
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16

srun /bin/sh -lc 'sleep 300'
"""

    validate_hpc_script(script)
