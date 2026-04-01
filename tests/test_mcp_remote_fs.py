from __future__ import annotations

import json
import hashlib

import pytest


def test_build_payload_command_keeps_python_header_left_aligned() -> None:
    from inspire.mcp.remote_fs import _build_payload_command

    command = _build_payload_command(
        "stat",
        {"path": "/etc/hostname"},
        "\nimport pathlib\nprint(pathlib.Path('/etc/hostname').read_text())\n",
    )
    script = command.split("<<'PY'\n", 1)[1]

    assert "\nimport base64\n" in script
    assert "\n        import base64\n" not in script


def test_read_file_returns_content_and_sha256(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.remote_fs as remote_fs_module

    content = "print('hello')\n"
    payload = {
        "ok": True,
        "content": content,
        "encoding": "utf-8",
        "size": len(content.encode("utf-8")),
        "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
        "truncated": False,
    }

    def fake_exec_remote_command(**kwargs):
        return {"ok": True, "exit_code": 0, "stdout": json.dumps(payload), "stderr": ""}

    monkeypatch.setattr(remote_fs_module, "exec_remote_command", fake_exec_remote_command)

    result = remote_fs_module.read_file(bridge="cpu-main", path="/tmp/demo.py")

    assert result["content"] == content
    assert result["sha256"] == payload["sha256"]
    assert result["size"] == payload["size"]


def test_edit_file_rejects_when_old_text_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.remote_fs as remote_fs_module

    monkeypatch.setattr(
        remote_fs_module,
        "read_file",
        lambda **kwargs: {
            "path": "/tmp/demo.txt",
            "content": "hello world\n",
            "encoding": "utf-8",
            "sha256": hashlib.sha256(b"hello world\n").hexdigest(),
        },
    )

    with pytest.raises(remote_fs_module.McpToolError) as exc_info:
        remote_fs_module.edit_file(
            bridge="cpu-main",
            path="/tmp/demo.txt",
            old_text="goodbye",
            new_text="hello",
        )

    assert exc_info.value.code == "edit_conflict"


def test_write_file_encodes_content_and_returns_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    import inspire.mcp.remote_fs as remote_fs_module

    captured = {}
    content = "alpha\nbeta\n"
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()

    def fake_exec_remote_command(**kwargs):
        captured.update(kwargs)
        return {
            "ok": True,
            "exit_code": 0,
            "stdout": json.dumps(
                {"ok": True, "path": "/tmp/out.txt", "size": 11, "sha256": sha256}
            ),
            "stderr": "",
        }

    monkeypatch.setattr(remote_fs_module, "exec_remote_command", fake_exec_remote_command)

    result = remote_fs_module.write_file(bridge="cpu-main", path="/tmp/out.txt", content=content)

    assert result["sha256"] == sha256
    assert result["size"] == 11
    assert "base64" in captured["command"]
    assert "/tmp/out.txt" in captured["command"]
