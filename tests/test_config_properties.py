"""Property-based tests for config loading layer.

These tests use hypothesis to generate random inputs and verify
invariants (properties) that should always hold for the config system.

Run with: uv run pytest tests/test_config_properties.py -v
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings, strategies as st

from inspire.config import Config


# ============================================================================
# Hypothesis Strategies
# ============================================================================

workspace_id_strategy = st.text(
    alphabet=st.characters(categories=["L", "N"], exclude_characters=" \t\n\r"),
    min_size=1,
    max_size=64,
).filter(lambda s: bool(s and s.strip()))

username_strategy = st.text(
    alphabet=st.characters(categories=["L", "N"], exclude_characters=" \t\n\r/@"),
    min_size=1,
    max_size=64,
).filter(lambda s: bool(s and s.strip()))

url_strategy = st.text(
    alphabet=st.characters(categories=["L", "N"], exclude_characters=" \t\n"),
    min_size=1,
    max_size=200,
).filter(lambda s: bool(s and s.strip()))

timeout_strategy = st.integers(min_value=1, max_value=300)

retry_delay_strategy = st.floats(
    min_value=0.1, max_value=30.0, allow_nan=False, allow_infinity=False
)

gpu_count_strategy = st.sampled_from([1, 2, 4, 8])

cpu_cores_strategy = st.integers(min_value=1, max_value=256)

memory_gb_strategy = st.integers(min_value=1, max_value=16000)

gpu_memory_gb_strategy = st.integers(min_value=1, max_value=200)

gpu_type_strategy = st.sampled_from(["H100", "H200"])

description_strategy = st.text(
    alphabet=st.characters(categories=["L", "N", "P"], exclude_characters="\x00"),
    min_size=0,
    max_size=256,
)


def make_spec_dict(
    spec_id: str,
    gpu_type: str,
    gpu_count: int,
    cpu_cores: int,
    memory_gb: int,
    gpu_memory_gb: int,
    description: str,
) -> dict[str, Any]:
    """Create a valid spec dict matching the workspace_specs structure."""
    return {
        "spec_id": spec_id,
        "gpu_type": gpu_type,
        "gpu_count": gpu_count,
        "cpu_cores": cpu_cores,
        "memory_gb": memory_gb,
        "gpu_memory_gb": gpu_memory_gb,
        "description": description,
    }


spec_dict_strategy = st.builds(
    make_spec_dict,
    spec_id=workspace_id_strategy,
    gpu_type=gpu_type_strategy,
    gpu_count=gpu_count_strategy,
    cpu_cores=cpu_cores_strategy,
    memory_gb=memory_gb_strategy,
    gpu_memory_gb=gpu_memory_gb_strategy,
    description=description_strategy,
)


# ============================================================================
# Config Field Round-Trip Tests
# ============================================================================


@given(username=username_strategy)
@settings(max_examples=100)
def test_username_round_trip(username: str) -> None:
    """Username should survive config instantiation."""
    config = Config(username=username, password="test")
    assert config.username == username


@given(timeout=timeout_strategy)
@settings(max_examples=100)
def test_timeout_values_preserved(timeout: int) -> None:
    """Timeout values should be preserved exactly."""
    config = Config(timeout=timeout, username="test", password="test")
    assert config.timeout == timeout
    assert isinstance(config.timeout, int)


@given(retry_delay=retry_delay_strategy)
@settings(max_examples=100)
def test_retry_delay_values_preserved(retry_delay: float) -> None:
    """Retry delay values should be preserved within floating point precision."""
    config = Config(retry_delay=retry_delay, username="test", password="test")
    assert abs(config.retry_delay - retry_delay) < 0.001


@given(base_url=url_strategy)
@settings(max_examples=100)
def test_base_url_round_trip(base_url: str) -> None:
    """Base URL should survive config instantiation."""
    config = Config(base_url=base_url, username="test", password="test")
    assert config.base_url == base_url


@given(target_dir=st.none() | url_strategy)
@settings(max_examples=100)
def test_target_dir_round_trip(target_dir: str | None) -> None:
    """Target dir (or None) should survive config instantiation."""
    config = Config(target_dir=target_dir, username="test", password="test")
    assert config.target_dir == target_dir


@given(ws_id=workspace_id_strategy)
@settings(max_examples=100)
def test_job_workspace_id_round_trip(ws_id: str) -> None:
    """Job workspace ID should survive config instantiation."""
    config = Config(job_workspace_id=ws_id, username="test", password="test")
    assert config.job_workspace_id == ws_id


@given(ws_id=workspace_id_strategy)
@settings(max_examples=100)
def test_default_workspace_id_round_trip(ws_id: str) -> None:
    """Default workspace ID should survive config instantiation."""
    config = Config(default_workspace_id=ws_id, username="test", password="test")
    assert config.default_workspace_id == ws_id


# ============================================================================
# Config Field Edge Case Tests
# ============================================================================


@given(ws_id=workspace_id_strategy)
@settings(max_examples=100)
def test_job_workspace_id_with_special_characters(ws_id: str) -> None:
    """Unicode, dashes, underscores in workspace IDs should be handled."""
    config = Config(job_workspace_id=ws_id, username="test", password="test")
    assert config.job_workspace_id == ws_id
    assert isinstance(config.job_workspace_id, str)


@given(
    ws_id=st.text(
        alphabet=st.characters(categories=["L", "N"]),
        min_size=64,
        max_size=64,
    )
)
@settings(max_examples=100)
def test_long_job_workspace_id_accepted(ws_id: str) -> None:
    """64-char workspace IDs should work."""
    config = Config(job_workspace_id=ws_id, username="test", password="test")
    assert config.job_workspace_id == ws_id
    assert len(config.job_workspace_id) == 64


@given(
    ws_id=st.text(
        alphabet=st.characters(categories=["L", "N"]),
        min_size=1,
        max_size=64,
    ).map(
        lambda s: s.replace(" ", "")
    )  # Ensure no spaces
)
@settings(max_examples=100)
def test_job_workspace_id_no_spaces_accepted(ws_id: str) -> None:
    """Workspace IDs without spaces should be preserved exactly."""
    if not ws_id:
        return  # Skip empty
    config = Config(job_workspace_id=ws_id, username="test", password="test")
    assert " " not in config.job_workspace_id
    assert config.job_workspace_id == ws_id


# ============================================================================
# workspace_specs Round-Trip Tests
# ============================================================================


@given(
    ws_id=workspace_id_strategy,
    spec=spec_dict_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_single_spec_round_trip(ws_id: str, spec: dict[str, Any]) -> None:
    """Single spec should survive config instantiation."""
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert ws_id in config.workspace_specs
    assert len(config.workspace_specs[ws_id]) == 1
    assert config.workspace_specs[ws_id][0]["spec_id"] == spec["spec_id"]
    assert config.workspace_specs[ws_id][0]["gpu_type"] == spec["gpu_type"]
    assert config.workspace_specs[ws_id][0]["gpu_count"] == spec["gpu_count"]


@given(
    ws_id=workspace_id_strategy,
    specs=st.lists(spec_dict_strategy, min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_workspace_spec_multiple_specs_round_trip(ws_id: str, specs: list[dict[str, Any]]) -> None:
    """Multiple specs for same workspace should be preserved."""
    workspace_specs = {ws_id: specs}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert ws_id in config.workspace_specs
    assert len(config.workspace_specs[ws_id]) == len(specs)
    for i, spec in enumerate(specs):
        assert config.workspace_specs[ws_id][i]["spec_id"] == spec["spec_id"]


@given(
    workspaces=st.dictionaries(
        keys=workspace_id_strategy,
        values=st.lists(spec_dict_strategy, min_size=1, max_size=5),
        max_size=5,
    )
)
@settings(max_examples=100)
def test_workspace_spec_multiple_workspaces_round_trip(
    workspaces: dict[str, list[dict[str, Any]]],
) -> None:
    """Multiple workspaces each should get correct specs."""
    config = Config(workspace_specs=workspaces, username="test", password="test")

    assert len(config.workspace_specs) == len(workspaces)
    for ws_id, specs in workspaces.items():
        assert ws_id in config.workspace_specs
        assert len(config.workspace_specs[ws_id]) == len(specs)


@given(
    ws_id=workspace_id_strategy,
    spec_id=workspace_id_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_spec_id_preserved(ws_id: str, spec_id: str) -> None:
    """spec_id should be preserved exactly."""
    spec = make_spec_dict(
        spec_id=spec_id,
        gpu_type="H200",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        description="test",
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["spec_id"] == spec_id


@given(
    ws_id=workspace_id_strategy,
    gpu_type=gpu_type_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_all_gpu_types(ws_id: str, gpu_type: str) -> None:
    """H100 and H200 GPU types should be preserved."""
    spec = make_spec_dict(
        spec_id="spec-1",
        gpu_type=gpu_type,
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        description="test",
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["gpu_type"] == gpu_type


@given(
    ws_id=workspace_id_strategy,
    gpu_count=gpu_count_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_gpu_counts(ws_id: str, gpu_count: int) -> None:
    """1, 2, 4, 8 GPU counts should be preserved."""
    spec = make_spec_dict(
        spec_id="spec-1",
        gpu_type="H200",
        gpu_count=gpu_count,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        description="test",
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["gpu_count"] == gpu_count


@given(
    ws_id=workspace_id_strategy,
    cpu_cores=cpu_cores_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_cpu_cores_preserved(ws_id: str, cpu_cores: int) -> None:
    """CPU core counts should be preserved."""
    spec = make_spec_dict(
        spec_id="spec-1",
        gpu_type="H200",
        gpu_count=1,
        cpu_cores=cpu_cores,
        memory_gb=200,
        gpu_memory_gb=141,
        description="test",
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["cpu_cores"] == cpu_cores


@given(
    ws_id=workspace_id_strategy,
    memory_gb=memory_gb_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_memory_gb_preserved(ws_id: str, memory_gb: int) -> None:
    """Memory GB values should be preserved."""
    spec = make_spec_dict(
        spec_id="spec-1",
        gpu_type="H200",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=memory_gb,
        gpu_memory_gb=141,
        description="test",
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["memory_gb"] == memory_gb


@given(
    ws_id=workspace_id_strategy,
    description=description_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_description_preserved(ws_id: str, description: str) -> None:
    """Description strings should be preserved."""
    spec = make_spec_dict(
        spec_id="spec-1",
        gpu_type="H200",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        description=description,
    )
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["description"] == description


@given(
    ws_id=workspace_id_strategy,
    spec=spec_dict_strategy,
)
@settings(max_examples=100)
def test_workspace_spec_gpu_memory_gb_preserved(ws_id: str, spec: dict[str, Any]) -> None:
    """GPU memory GB should be preserved."""
    workspace_specs = {ws_id: [spec]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id][0]["gpu_memory_gb"] == spec["gpu_memory_gb"]


# ============================================================================
# workspace_names Round-Trip Tests
# ============================================================================


@given(
    ws_id=workspace_id_strategy,
    ws_name=st.text(min_size=1, max_size=256),
)
@settings(max_examples=100)
def test_workspace_names_single_round_trip(ws_id: str, ws_name: str) -> None:
    """Single workspace name should be preserved."""
    workspace_names = {ws_id: ws_name}
    config = Config(workspace_names=workspace_names, username="test", password="test")

    assert ws_id in config.workspace_names
    assert config.workspace_names[ws_id] == ws_name


@given(
    workspace_names=st.dictionaries(
        keys=workspace_id_strategy,
        values=st.text(min_size=1, max_size=256),
        max_size=10,
    )
)
@settings(max_examples=100)
def test_workspace_names_multiple_round_trip(workspace_names: dict[str, str]) -> None:
    """Multiple workspace names should be preserved."""
    config = Config(workspace_names=workspace_names, username="test", password="test")

    assert len(config.workspace_names) == len(workspace_names)
    for ws_id, name in workspace_names.items():
        assert config.workspace_names.get(ws_id) == name


# ============================================================================
# compute_groups Round-Trip Tests
# ============================================================================


@given(
    compute_groups=st.lists(
        st.fixed_dictionaries(
            {
                "name": st.text(min_size=1, max_size=64),
                "id": workspace_id_strategy,
                "gpu_type": gpu_type_strategy,
                "location": st.text(min_size=0, max_size=128),
            }
        ),
        min_size=0,
        max_size=10,
    )
)
@settings(max_examples=100)
def test_compute_groups_structure_preserved(compute_groups: list[dict[str, Any]]) -> None:
    """compute_groups list/dict structure should survive."""
    config = Config(compute_groups=compute_groups, username="test", password="test")

    assert len(config.compute_groups) == len(compute_groups)
    for i, cg in enumerate(compute_groups):
        assert config.compute_groups[i].get("name") == cg["name"]
        assert config.compute_groups[i].get("id") == cg["id"]


# ============================================================================
# remote_env Round-Trip Tests
# ============================================================================


@given(
    remote_env=st.dictionaries(
        keys=st.text(min_size=1, max_size=64, alphabet=st.characters(categories=["L"])),
        values=st.text(min_size=0, max_size=512),
        max_size=20,
    )
)
@settings(max_examples=100)
def test_remote_env_structure_preserved(remote_env: dict[str, str]) -> None:
    """remote_env dict structure should survive."""
    config = Config(remote_env=remote_env, username="test", password="test")

    assert len(config.remote_env) == len(remote_env)
    for key, value in remote_env.items():
        assert config.remote_env.get(key) == value


# ============================================================================
# Integration Property Tests
# ============================================================================


@given(
    ws_id_1=workspace_id_strategy,
    ws_id_2=workspace_id_strategy,
)
@settings(max_examples=100)
def test_spec_with_different_gpu_types_per_workspace(ws_id_1: str, ws_id_2: str) -> None:
    """Each workspace can have different GPU types."""
    if ws_id_1 == ws_id_2:
        return  # Skip same workspace

    spec_1 = make_spec_dict(
        spec_id="spec-1",
        gpu_type="H100",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=80,
        description="H100 spec",
    )
    spec_2 = make_spec_dict(
        spec_id="spec-2",
        gpu_type="H200",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=141,
        description="H200 spec",
    )

    workspace_specs = {ws_id_1: [spec_1], ws_id_2: [spec_2]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert config.workspace_specs[ws_id_1][0]["gpu_type"] == "H100"
    assert config.workspace_specs[ws_id_2][0]["gpu_type"] == "H200"


@given(
    ws_id=workspace_id_strategy,
    specs=st.lists(spec_dict_strategy, min_size=1, max_size=10),
)
@settings(max_examples=100)
def test_spec_counts_consistent_across_load(ws_id: str, specs: list[dict[str, Any]]) -> None:
    """Number of specs should be consistent after round-trip."""
    workspace_specs = {ws_id: specs}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    assert len(config.workspace_specs[ws_id]) == len(specs)


@given(
    workspace_specs=st.dictionaries(
        keys=workspace_id_strategy,
        values=st.lists(spec_dict_strategy, min_size=1, max_size=5),
        max_size=5,
    )
)
@settings(max_examples=100)
def test_workspace_id_key_exact_match(workspace_specs: dict[str, list[dict[str, Any]]]) -> None:
    """Workspace IDs should be used as exact keys, not fuzzy matched."""
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    for ws_id in workspace_specs:
        assert ws_id in config.workspace_specs
        assert ws_id not in [k for k in config.workspace_specs if k != ws_id]


@given(
    ws_id=workspace_id_strategy,
    spec_id=st.text(min_size=1, max_size=64),
)
@settings(max_examples=100)
def test_spec_id_unique_per_workspace(ws_id: str, spec_id: str) -> None:
    """Same spec_id can exist in different workspaces."""
    spec_1 = make_spec_dict(
        spec_id=spec_id,
        gpu_type="H100",
        gpu_count=1,
        cpu_cores=15,
        memory_gb=200,
        gpu_memory_gb=80,
        description="H100 spec",
    )
    spec_2 = make_spec_dict(
        spec_id=spec_id,
        gpu_type="H200",
        gpu_count=2,
        cpu_cores=30,
        memory_gb=400,
        gpu_memory_gb=141,
        description="H200 spec",
    )

    workspace_specs = {ws_id: [spec_1, spec_2]}
    config = Config(workspace_specs=workspace_specs, username="test", password="test")

    spec_ids = [s["spec_id"] for s in config.workspace_specs[ws_id]]
    assert spec_ids.count(spec_id) == 2
