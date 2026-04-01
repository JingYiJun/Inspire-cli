"""Remote file-system helpers implemented over bridge exec."""

from __future__ import annotations

import base64
import json
from textwrap import dedent
from typing import Any

from .errors import McpToolError
from .runtime import exec_remote_command


def _build_python_command(script: str) -> str:
    return "python3 - <<'PY'\n" + script.rstrip() + "\nPY"


def _build_payload_command(operation: str, payload: dict[str, Any], body: str) -> str:
    payload_b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    path_hint = str(payload.get("path") or "")
    prelude = "\n".join(
        [
            f"# operation: {operation}",
            f"# path: {path_hint}",
            "import base64",
            "import json",
            "",
            f'payload = json.loads(base64.b64decode({payload_b64!r}).decode("utf-8"))',
        ]
    )
    script = prelude + "\n" + body.strip("\n") + "\n"
    return _build_python_command(script)


def _extract_json_payload(result: dict[str, Any], *, bridge: str) -> dict[str, Any]:
    if result["exit_code"] != 0:
        stderr = str(result.get("stderr") or "").strip()
        if result["exit_code"] == 127 or "python3" in stderr.lower():
            raise McpToolError(
                "remote_python_required",
                "Remote bridge requires python3 for MCP file tools.",
                {"bridge": bridge, "stderr": stderr},
            )
        raise McpToolError(
            "remote_command_failed",
            stderr or f"Remote command failed with exit code {result['exit_code']}",
            {"bridge": bridge, "exit_code": result["exit_code"]},
        )

    stdout = str(result.get("stdout") or "").strip()
    try:
        payload = json.loads(stdout or "{}")
    except json.JSONDecodeError as exc:
        raise McpToolError(
            "remote_command_failed",
            "Remote command did not return valid JSON.",
            {"bridge": bridge, "stdout": stdout[:400]},
        ) from exc

    if payload.get("ok", True) is False:
        code = str(payload.get("code") or "remote_command_failed")
        message = str(payload.get("message") or "Remote file operation failed.")
        raise McpToolError(code, message, {"bridge": bridge, "payload": payload})

    return payload


def read_file(
    *,
    bridge: str,
    path: str,
    offset: int = 0,
    limit: int | None = None,
    encoding: str = "utf-8",
) -> dict[str, Any]:
    body = dedent(
        """
        import hashlib
        import pathlib

        file_path = pathlib.Path(payload["path"])
        if not file_path.exists():
            print(json.dumps({
                "ok": False,
                "code": "file_not_found",
                "message": f"Remote file not found: {file_path}",
            }))
            raise SystemExit(0)
        data = file_path.read_bytes()
        start = max(0, int(payload.get("offset", 0) or 0))
        raw_limit = payload.get("limit")
        end = None if raw_limit is None else start + max(0, int(raw_limit))
        chunk = data[start:end]
        print(json.dumps({
            "ok": True,
            "path": str(file_path),
            "content": chunk.decode(payload.get("encoding") or "utf-8"),
            "encoding": payload.get("encoding") or "utf-8",
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "truncated": end is not None and len(data) > end,
        }))
        """
    )
    command = _build_payload_command(
        "read_file",
        {"path": path, "offset": offset, "limit": limit, "encoding": encoding},
        body,
    )
    result = exec_remote_command(bridge=bridge, command=command)
    return _extract_json_payload(result, bridge=bridge)


def write_file(
    *,
    bridge: str,
    path: str,
    content: str,
    encoding: str = "utf-8",
    atomic: bool = True,
    mkdir_parent: bool = False,
    mode: int | None = None,
) -> dict[str, Any]:
    content_b64 = base64.b64encode(content.encode(encoding)).decode("ascii")
    body = dedent(
        """
        import hashlib
        import os
        import pathlib
        import tempfile

        file_path = pathlib.Path(payload["path"])
        parent = file_path.parent
        if payload.get("mkdir_parent"):
            parent.mkdir(parents=True, exist_ok=True)
        elif not parent.exists():
            print(json.dumps({
                "ok": False,
                "code": "file_not_found",
                "message": f"Remote parent directory not found: {parent}",
            }))
            raise SystemExit(0)
        raw = base64.b64decode(payload["content_b64"])
        if payload.get("atomic", True):
            with tempfile.NamedTemporaryFile(dir=str(parent), delete=False) as handle:
                handle.write(raw)
                temp_path = handle.name
            os.replace(temp_path, file_path)
        else:
            file_path.write_bytes(raw)
        mode = payload.get("mode")
        if mode is not None:
            os.chmod(file_path, int(mode))
        data = file_path.read_bytes()
        print(json.dumps({
            "ok": True,
            "path": str(file_path),
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }))
        """
    )
    command = _build_payload_command(
        "write_file",
        {
            "path": path,
            "content_b64": content_b64,
            "encoding": encoding,
            "atomic": atomic,
            "mkdir_parent": mkdir_parent,
            "mode": mode,
        },
        body,
    )
    result = exec_remote_command(bridge=bridge, command=command)
    payload = _extract_json_payload(result, bridge=bridge)
    payload["encoding"] = encoding
    return payload


def edit_file(
    *,
    bridge: str,
    path: str,
    old_text: str,
    new_text: str,
    replace_all: bool = False,
    expected_sha256: str | None = None,
) -> dict[str, Any]:
    existing = read_file(bridge=bridge, path=path)
    if expected_sha256 and expected_sha256 != existing["sha256"]:
        raise McpToolError(
            "stale_read",
            f"Remote file changed since last read: {path}",
            {"bridge": bridge, "path": path, "expected_sha256": expected_sha256},
        )

    content = str(existing["content"])
    occurrences = content.count(old_text)
    if occurrences == 0:
        raise McpToolError(
            "edit_conflict",
            f"Could not find target text in remote file: {path}",
            {"bridge": bridge, "path": path},
        )

    updated = content.replace(old_text, new_text, -1 if replace_all else 1)
    write_result = write_file(
        bridge=bridge,
        path=path,
        content=updated,
        encoding=str(existing.get("encoding") or "utf-8"),
    )
    write_result["replacements"] = occurrences if replace_all else 1
    return write_result


def list_dir(
    *,
    bridge: str,
    path: str,
    recursive: bool = False,
    limit: int = 200,
    include_hidden: bool = False,
) -> dict[str, Any]:
    body = dedent(
        """
        import pathlib

        root = pathlib.Path(payload["path"])
        if not root.exists():
            print(json.dumps({
                "ok": False,
                "code": "file_not_found",
                "message": f"Remote path not found: {root}",
            }))
            raise SystemExit(0)
        if not root.is_dir():
            print(json.dumps({
                "ok": False,
                "code": "remote_command_failed",
                "message": f"Remote path is not a directory: {root}",
            }))
            raise SystemExit(0)
        iterator = root.rglob("*") if payload.get("recursive") else root.iterdir()
        entries = []
        for entry in iterator:
            name = entry.name
            if not payload.get("include_hidden") and name.startswith("."):
                continue
            kind = "symlink" if entry.is_symlink() else "directory" if entry.is_dir() else "file"
            stats = entry.lstat()
            entries.append({
                "path": str(entry),
                "name": name,
                "type": kind,
                "size": int(stats.st_size),
                "mode": int(stats.st_mode),
            })
            if len(entries) >= int(payload.get("limit", 200)):
                break
        print(json.dumps({"ok": True, "path": str(root), "entries": entries}))
        """
    )
    command = _build_payload_command(
        "list_dir",
        {
            "path": path,
            "recursive": recursive,
            "limit": limit,
            "include_hidden": include_hidden,
        },
        body,
    )
    result = exec_remote_command(bridge=bridge, command=command)
    return _extract_json_payload(result, bridge=bridge)


def stat(
    *,
    bridge: str,
    path: str,
    follow_symlinks: bool = False,
) -> dict[str, Any]:
    body = dedent(
        """
        import pathlib

        target = pathlib.Path(payload["path"])
        if not target.exists() and not target.is_symlink():
            print(json.dumps({"ok": True, "path": str(target), "exists": False}))
            raise SystemExit(0)
        stats = target.stat() if payload.get("follow_symlinks") else target.lstat()
        if target.is_symlink():
            kind = "symlink"
        elif target.is_dir():
            kind = "directory"
        else:
            kind = "file"
        print(json.dumps({
            "ok": True,
            "path": str(target),
            "exists": True,
            "type": kind,
            "size": int(stats.st_size),
            "mode": int(stats.st_mode),
            "mtime": float(stats.st_mtime),
        }))
        """
    )
    command = _build_payload_command(
        "stat",
        {"path": path, "follow_symlinks": follow_symlinks},
        body,
    )
    result = exec_remote_command(bridge=bridge, command=command)
    return _extract_json_payload(result, bridge=bridge)
