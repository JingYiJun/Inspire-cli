"""Inspire Training Platform SDK."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable

__version__ = "0.2.4"


def _run_in_fresh_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    result: dict[str, Any] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = func(*args, **kwargs)
        except BaseException as exc:  # pragma: no cover - re-raised in caller thread
            error["exc"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if "exc" in error:
        raise error["exc"]
    return result.get("value")


if not getattr(asyncio, "_inspire_nested_run_patched", False):
    _original_asyncio_run = asyncio.run

    def _asyncio_run_compat(main, *, debug=None, loop_factory=None):  # type: ignore[no-untyped-def]
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return _original_asyncio_run(main, debug=debug, loop_factory=loop_factory)
        return _run_in_fresh_thread(
            _original_asyncio_run,
            main,
            debug=debug,
            loop_factory=loop_factory,
        )

    asyncio.run = _asyncio_run_compat
    asyncio._inspire_nested_run_patched = True  # type: ignore[attr-defined]


try:  # pragma: no cover - optional runtime dependency during import
    import anyio
    from sniffio import current_async_library

    if not getattr(anyio, "_inspire_nested_run_patched", False):
        _original_anyio_run = anyio.run

        def _anyio_run_compat(  # type: ignore[no-untyped-def]
            func,
            *args,
            backend: str = "asyncio",
            backend_options=None,
        ):
            try:
                active_library = current_async_library()
            except Exception:
                active_library = None
            if active_library:
                return _run_in_fresh_thread(
                    _original_anyio_run,
                    func,
                    *args,
                    backend=backend,
                    backend_options=backend_options,
                )
            try:
                return _original_anyio_run(
                    func,
                    *args,
                    backend=backend,
                    backend_options=backend_options,
                )
            except RuntimeError as exc:
                if "Already running" not in str(exc):
                    raise
                return _run_in_fresh_thread(
                    _original_anyio_run,
                    func,
                    *args,
                    backend=backend,
                    backend_options=backend_options,
                )

        anyio.run = _anyio_run_compat
        anyio._inspire_nested_run_patched = True  # type: ignore[attr-defined]
except Exception:
    pass
