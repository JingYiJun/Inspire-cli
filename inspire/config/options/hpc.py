"""Config options: HPC."""

from __future__ import annotations

from inspire.config.schema_models import ConfigOption, _parse_int

HPC_OPTIONS: list[ConfigOption] = [
    ConfigOption(
        env_var="INSPIRE_WORKSPACE_HPC_ID",
        toml_key="workspaces.hpc",
        field_name="workspace_hpc_id",
        description="Workspace ID for HPC workloads",
        default=None,
        category="Workspaces",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_HPC_IMAGE",
        toml_key="hpc.image",
        field_name="hpc_image",
        description="Default Docker image for HPC jobs",
        default=None,
        category="HPC",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_HPC_IMAGE_TYPE",
        toml_key="hpc.image_type",
        field_name="hpc_image_type",
        description="Default image type for HPC jobs",
        default="SOURCE_PUBLIC",
        category="HPC",
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_HPC_PRIORITY",
        toml_key="hpc.priority",
        field_name="hpc_priority",
        description="Default priority for HPC jobs",
        default=4,
        category="HPC",
        parser=_parse_int,
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_HPC_TTL_AFTER_FINISH_SECONDS",
        toml_key="hpc.ttl_after_finish_seconds",
        field_name="hpc_ttl_after_finish_seconds",
        description="Default retain time after HPC jobs finish",
        default=600,
        category="HPC",
        parser=_parse_int,
        scope="project",
    ),
    ConfigOption(
        env_var="INSPIRE_HPC_DEFAULT_PRESET",
        toml_key="hpc.default_preset",
        field_name="hpc_default_preset",
        description="Default preset name for HPC jobs",
        default=None,
        category="HPC",
        scope="project",
    ),
]
