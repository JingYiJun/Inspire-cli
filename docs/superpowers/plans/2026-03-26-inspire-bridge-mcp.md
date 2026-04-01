# Inspire Bridge MCP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在当前仓库内新增一个基于现有 bridge/tunnel 能力的 MCP 服务器，为通用 agent 提供远程 `exec/read/write/edit/list/stat` 工具。

**Architecture:** 新增 `inspire/mcp/` 包作为与 Click CLI 平行的协议入口。底层复用 `inspire.bridge.tunnel.ssh_exec` 执行远程命令，并在 `runtime` / `remote_fs` 中封装结构化错误、JSON 解析与远程文件抽象；`server.py` 负责 MCP 工具注册和 stdio 启动。

**Tech Stack:** Python 3.10+, `mcp` Python SDK, pytest, 现有 Inspire bridge/tunnel domain 模块。

---

### Task 1: Add dependency and entrypoint

**Files:**
- Modify: `pyproject.toml`
- Create: `inspire/mcp/__init__.py`

- [ ] **Step 1: Write the failing test**

```python
def test_inspire_mcp_entrypoint_registered():
    scripts = project_config["project"]["scripts"]
    assert scripts["inspire-mcp"] == "inspire.mcp.server:main"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py::test_inspire_mcp_entrypoint_registered -v`
Expected: FAIL because script/module does not exist.

- [ ] **Step 3: Write minimal implementation**

Add `mcp` dependency, add `inspire-mcp` script, create `inspire/mcp/__init__.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py::test_inspire_mcp_entrypoint_registered -v`
Expected: PASS.

### Task 2: Build runtime wrapper

**Files:**
- Create: `inspire/mcp/errors.py`
- Create: `inspire/mcp/runtime.py`
- Test: `tests/test_mcp_runtime.py`

- [ ] **Step 1: Write the failing test**

```python
def test_exec_remote_command_wraps_cwd_env_and_bridge():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_runtime.py -v`
Expected: FAIL because module is missing.

- [ ] **Step 3: Write minimal implementation**

Implement runtime helpers for command wrapping, bridge execution, result normalization, and exception mapping.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_runtime.py -v`
Expected: PASS.

### Task 3: Build remote file abstraction

**Files:**
- Create: `inspire/mcp/remote_fs.py`
- Test: `tests/test_mcp_remote_fs.py`

- [ ] **Step 1: Write the failing test**

```python
def test_read_file_returns_content_and_sha256():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_remote_fs.py -v`
Expected: FAIL because file API is missing.

- [ ] **Step 3: Write minimal implementation**

Implement `read_file`, `write_file`, `edit_file`, `list_dir`, `stat` using remote `python3` snippets over SSH.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_remote_fs.py -v`
Expected: PASS.

### Task 4: Register MCP tools and stdio server

**Files:**
- Create: `inspire/mcp/server.py`
- Test: `tests/test_mcp_server.py`

- [ ] **Step 1: Write the failing test**

```python
def test_build_server_registers_expected_tools():
    ...
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: FAIL because server builder is missing.

- [ ] **Step 3: Write minimal implementation**

Create MCP server, register six tools, expose `main()` stdio entry.

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/test_mcp_server.py -v`
Expected: PASS.

### Task 5: Verify the full slice

**Files:**
- Modify: `pyproject.toml`
- Create: `inspire/mcp/__init__.py`
- Create: `inspire/mcp/errors.py`
- Create: `inspire/mcp/runtime.py`
- Create: `inspire/mcp/remote_fs.py`
- Create: `inspire/mcp/server.py`
- Create: `tests/test_mcp_runtime.py`
- Create: `tests/test_mcp_remote_fs.py`
- Create: `tests/test_mcp_server.py`

- [ ] **Step 1: Run targeted tests**

Run: `uv run pytest tests/test_mcp_runtime.py tests/test_mcp_remote_fs.py tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 2: Run lint on changed files**

Run: `uv run ruff check inspire/mcp tests/test_mcp_runtime.py tests/test_mcp_remote_fs.py tests/test_mcp_server.py`
Expected: PASS.

- [ ] **Step 3: Run broader related tests**

Run: `uv run pytest tests/test_ssh_exec.py tests/test_bridge_exec.py tests/test_bridge_scp.py -v`
Expected: PASS.
