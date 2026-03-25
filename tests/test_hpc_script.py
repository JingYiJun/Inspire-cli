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

    assert script.startswith("#!/bin/bash\n")
    assert "#SBATCH -o /hpc_logs/slurm-%j.out" in script
    assert "#SBATCH -e /hpc_logs/slurm-%j.err" in script
    assert "#SBATCH --ntasks=2" in script
    assert "#SBATCH --cpus-per-task=8" in script
    assert "#SBATCH --mem=64G" in script
    assert "#SBATCH --time=0-12:00:00" in script
    assert "#SBATCH --partition=hpc" in script
    assert "srun bash -lc 'python main.py'" in script


def test_validate_hpc_script_rejects_script_without_srun() -> None:
    with pytest.raises(ValueError, match="srun"):
        validate_hpc_script("#!/bin/bash\n#SBATCH --time=0-01:00:00\npython main.py\n")
