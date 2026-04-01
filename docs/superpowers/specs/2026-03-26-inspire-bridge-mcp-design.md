# Inspire Bridge MCP 设计

## 目标
在当前仓库内新增一个本地运行的 MCP 服务器，复用现有 `inspire bridge` / tunnel 能力，为通用 agent 暴露接近 harness 原生的远程执行与远程文件系统工具：`exec`、`read_file`、`write_file`、`edit_file`、`list_dir`、`stat`。

该服务的主要消费者是 Codex/harness 一类通用 agent，因此协议层应尽量隐藏 Inspire/bridge 细节；唯一要求 agent 显式提供 `bridge` 名称。

## 非目标
- 不实现交互式 PTY shell
- 不实现远端 sidecar daemon
- 不实现远程 `apply_patch`
- 第一版不做大文件上传/下载工具
- 不修改现有 CLI 命令的用户语义

## 现状与复用点
现有仓库已具备稳定的 SSH/tunnel 底座：
- `inspire/bridge/tunnel/ssh_exec.py`：核心 SSH 执行与流式输出
- `inspire/bridge/tunnel/models.py`：bridge 配置与解析
- `inspire/bridge/tunnel/scp.py`：后续可复用的大文件传输
- `inspire/cli/commands/bridge/*.py`：CLI 封装与错误语义参考

因此 MCP 层应做成薄适配层，负责：
- MCP 工具注册与 JSON schema
- 请求参数校验
- 远程文件系统抽象
- 错误映射与结构化返回

## 总体结构
新增独立包 `inspire/mcp/`，不与现有 Click CLI 强耦合。

建议文件：
- `inspire/mcp/__init__.py`
- `inspire/mcp/server.py`：MCP server 启动与工具注册
- `inspire/mcp/runtime.py`：bridge 解析、命令执行、错误映射、输出截断
- `inspire/mcp/remote_fs.py`：远程文件系统抽象
- `inspire/mcp/tools.py`：工具实现（或薄封装）
- `inspire/mcp/errors.py`：MCP 结构化错误

入口通过 `pyproject.toml` 新增 script：
- `inspire-mcp = "inspire.mcp.server:main"`

并新增主依赖 `mcp`。

## 工具设计
### 1. `exec`
入参：
- `bridge`：必填
- `command`：必填
- `cwd`：可选
- `env`：可选键值对
- `timeout_s`：可选
- `stream`：可选，第一版接受但仍返回聚合输出

行为：
- 若提供 `cwd`，远端命令会先 `cd` 再执行
- `env` 以 shell export 前缀注入
- 底层复用 `run_ssh_command()`
- 返回：`ok`、`bridge`、`cwd`、`exit_code`、`stdout`、`stderr`

### 2. `read_file`
入参：
- `bridge`、`path` 必填
- `offset`、`limit`、`encoding` 可选

行为：
- 仅支持文本读取
- 服务端通过远端 `python3` 脚本读取并返回 JSON，避免 shell quoting 与换行问题
- 返回内容、大小、`sha256`、是否截断

### 3. `write_file`
入参：
- `bridge`、`path`、`content` 必填
- `encoding`、`atomic`、`mkdir_parent`、`mode` 可选

行为：
- 服务端本地先 base64 编码内容，再调用远端 `python3` 原子写入
- `atomic=true` 时：写入同目录临时文件后 `os.replace`
- 可选创建父目录
- 返回最终大小与 `sha256`

### 4. `edit_file`
入参：
- `bridge`、`path`、`old_text`、`new_text` 必填
- `replace_all`、`expected_sha256` 可选

行为：
- 先走 `read_file`
- 在服务端本地执行替换
- 校验：`old_text` 是否命中；若提供 `expected_sha256`，需与当前远端文件一致
- 再走 `write_file`
- 返回替换次数、新 `sha256`

### 5. `list_dir`
入参：
- `bridge`、`path` 必填
- `recursive`、`limit`、`include_hidden` 可选

行为：
- 通过远端 `python3` 遍历目录
- 返回条目数组：`path`、`name`、`type`、`size`、`mode`

### 6. `stat`
入参：
- `bridge`、`path` 必填
- `follow_symlinks` 可选

行为：
- 返回 `exists`、`type`、`size`、`mode`、`mtime` 等

## 关键取舍
### 使用远端 `python3`
第一版远程文件操作统一依赖远端 `python3`。理由：
- 现有 bridge 面向开发环境，Python 存在概率高
- 文本读写、base64、JSON 输出、原子替换实现显著更稳
- 比拼接 `sed/head/tail/dd` 更可维护

若远端缺少 `python3`，工具返回清晰错误，提示用户桥接的远端环境不满足 MCP 文件工具要求；`exec` 本身仍可用。

### 不默认使用 `INSPIRE_TARGET_DIR`
因为用户明确要求“远程任意路径，跟本地一样”。因此：
- 所有文件工具都按显式 `path` 操作
- `exec` 只有在调用方传入 `cwd` 时才 `cd`
- MCP 不偷改当前目录

### 结构化错误
统一错误类别：
- `bridge_not_found`
- `tunnel_unavailable`
- `timeout`
- `remote_command_failed`
- `file_not_found`
- `edit_conflict`
- `stale_read`
- `remote_python_required`

## 测试策略
新增测试：
- `tests/test_mcp_runtime.py`
- `tests/test_mcp_remote_fs.py`
- `tests/test_mcp_server.py`

优先覆盖：
- bridge 解析与错误映射
- `exec` 的 `cwd/env/timeout`
- `read_file` / `write_file` / `edit_file` 的命令拼装与 JSON 解析
- `edit_file` 冲突与 `expected_sha256` 保护
- MCP server 工具注册

## 实施顺序
1. 新增依赖与入口
2. 先写 runtime / remote_fs 的失败测试
3. 实现 runtime / remote_fs
4. 新增 MCP server 与工具注册测试
5. 实现 server
6. 跑定向测试，再跑相关 lint/更广测试
