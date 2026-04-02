"""Execution-based tests for ETXTBSY (Text file busy) during rtunnel setup.

Reproduces the scenario where a running rtunnel process prevents ``cp`` from
overwriting the binary.  Since modern kernels (6.x) no longer raise ETXTBSY
for ``open(O_WRONLY|O_TRUNC)``, we simulate the failure with a ``cp`` wrapper
that mimics the error when the target still exists.

The fix uses ``rm -f`` before ``cp`` to unlink the directory entry — the kernel
keeps the old inode alive for the running process.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest

from inspire.platform.web.browser_api.rtunnel.commands import (
    _build_rtunnel_bin_lines,
)

_TEST_PORT = "53133"
_RTUNNEL_BIN = Path(f"/tmp/rtunnel-{_TEST_PORT}")


def _make_sleep_binary(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nsleep 300\n")
    path.chmod(0o755)


def _start_background_process(binary: Path) -> subprocess.Popen:
    proc = subprocess.Popen(
        [str(binary)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Give the process time to start and verify it's actually running
    time.sleep(0.1)
    assert proc.poll() is None, f"Background process failed to start (exit code: {proc.returncode})"
    return proc


def _run_shell(
    script: str,
    *,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    run_env = os.environ.copy()
    run_env["PORT"] = _TEST_PORT
    if env:
        run_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env=run_env,
        timeout=10,
    )


def _create_fake_cp(bin_dir: Path) -> Path:
    fake_cp = bin_dir / "cp"
    fake_cp.write_text(
        "#!/bin/sh\n"
        "# Simulate ETXTBSY: fail if the target file still exists on disk.\n"
        "# After `rm -f` unlinks it, the target no longer exists and real cp runs.\n"
        'for _last in "$@"; do :; done\n'
        'if [ -e "$_last" ]; then\n'
        "  echo \"cp: cannot create regular file '$_last': Text file busy\" >&2\n"
        "  exit 1\n"
        "fi\n"
        '/bin/cp "$@"\n'
    )
    fake_cp.chmod(0o755)
    return fake_cp


def _path_with_fake_cp(tmp_path: Path) -> str:
    fake_cp_dir = tmp_path / "fake-bin"
    fake_cp_dir.mkdir()
    _create_fake_cp(fake_cp_dir)
    return f"{fake_cp_dir}:{os.environ['PATH']}"


@pytest.fixture(autouse=True)
def _cleanup():
    _RTUNNEL_BIN.unlink(missing_ok=True)
    yield
    _RTUNNEL_BIN.unlink(missing_ok=True)


class TestSimulatedETXTBSY:
    """Simulate ETXTBSY with a fake cp that fails when target file exists.

    Without ``rm -f``, the fake cp sees the target and returns ETXTBSY.
    With ``rm -f`` (the fix), the target is unlinked first, so the fake cp
    falls through to ``/bin/cp`` and succeeds.
    """

    def test_rtunnel_bin_cp_succeeds_with_rm_f_fix(self, tmp_path: Path) -> None:
        _make_sleep_binary(_RTUNNEL_BIN)
        proc = _start_background_process(_RTUNNEL_BIN)

        try:
            source = tmp_path / "rtunnel-source"
            _make_sleep_binary(source)
            source.chmod(0o644)

            lines = _build_rtunnel_bin_lines(
                rtunnel_bin=str(source),
                contents_api_filename=None,
            )
            script = "\n".join(lines)
            result = _run_shell(script, env={"PATH": _path_with_fake_cp(tmp_path)})

            assert result.returncode == 0, (
                f"Script should succeed with rm -f fix: "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
            assert "Text file busy" not in result.stderr
            assert proc.poll() is None, "User A's process should still be running"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_contents_api_cp_succeeds_with_rm_f_fix(self, tmp_path: Path) -> None:
        _make_sleep_binary(_RTUNNEL_BIN)
        proc = _start_background_process(_RTUNNEL_BIN)

        try:
            _RTUNNEL_BIN.chmod(0o644)

            uploaded_file = tmp_path / ".inspire_rtunnel_bin"
            _make_sleep_binary(uploaded_file)

            lines = _build_rtunnel_bin_lines(
                rtunnel_bin=None,
                contents_api_filename=".inspire_rtunnel_bin",
            )
            script = "\n".join(lines)
            result = _run_shell(
                script,
                env={
                    "PATH": _path_with_fake_cp(tmp_path),
                    "HOME": str(tmp_path),
                },
            )

            assert result.returncode == 0, (
                f"Script should succeed with rm -f fix: "
                f"stdout={result.stdout!r} stderr={result.stderr!r}"
            )
            assert "Text file busy" not in result.stderr
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_without_rm_f_the_cp_would_fail(self, tmp_path: Path) -> None:
        """Prove that without rm -f, the fake cp correctly simulates ETXTBSY."""
        _make_sleep_binary(_RTUNNEL_BIN)
        proc = _start_background_process(_RTUNNEL_BIN)

        try:
            source = tmp_path / "rtunnel-source"
            _make_sleep_binary(source)
            source.chmod(0o644)

            script = (
                f"RTUNNEL_BIN_PATH={source}\n"
                f'RTUNNEL_BIN="/tmp/rtunnel-$PORT"\n'
                f'if [ -f "$RTUNNEL_BIN_PATH" ]; then '
                f'cp "$RTUNNEL_BIN_PATH" "$RTUNNEL_BIN"; fi'
            )
            result = _run_shell(script, env={"PATH": _path_with_fake_cp(tmp_path)})

            assert result.returncode != 0, "cp should have failed with ETXTBSY"
            assert "Text file busy" in result.stderr
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestMultiUserSameNotebook:
    """Simulate two users connecting to the same notebook.

    User A already has rtunnel running.  User B runs the setup script.
    User B's setup should succeed without disturbing User A.
    """

    def test_second_user_setup_succeeds(self, tmp_path: Path) -> None:
        _make_sleep_binary(_RTUNNEL_BIN)
        proc = _start_background_process(_RTUNNEL_BIN)

        try:
            source = tmp_path / "rtunnel-source"
            _make_sleep_binary(source)
            source.chmod(0o644)

            lines = _build_rtunnel_bin_lines(
                rtunnel_bin=str(source),
                contents_api_filename=None,
            )
            script = "\n".join(lines)
            result = _run_shell(script, env={"PATH": _path_with_fake_cp(tmp_path)})

            assert (
                result.returncode == 0
            ), f"Second user setup failed: stdout={result.stdout!r} stderr={result.stderr!r}"
            assert proc.poll() is None, "User A's process should still be running"
        finally:
            proc.terminate()
            proc.wait(timeout=5)


class TestRmFUnlinksRunningBinary:
    """Verify rm -f semantics: unlinking a running binary doesn't kill the process."""

    def test_rm_f_does_not_kill_running_process(self, tmp_path: Path) -> None:
        binary = tmp_path / "sleeper"
        _make_sleep_binary(binary)
        proc = _start_background_process(binary)

        try:
            result = subprocess.run(
                ["rm", "-f", str(binary)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.returncode == 0
            assert not binary.exists(), "File should be unlinked"
            assert proc.poll() is None, "Process should still be running"
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_new_file_after_rm_f_is_independent(self, tmp_path: Path) -> None:
        binary = tmp_path / "sleeper"
        _make_sleep_binary(binary)
        proc = _start_background_process(binary)

        try:
            os.unlink(str(binary))

            new_binary = tmp_path / "sleeper"
            new_binary.write_text("#!/bin/sh\necho new\n")
            new_binary.chmod(0o755)

            assert proc.poll() is None, "Old process should still be running"

            result = subprocess.run(
                [str(new_binary)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            assert result.stdout.strip() == "new"
        finally:
            proc.terminate()
            proc.wait(timeout=5)
