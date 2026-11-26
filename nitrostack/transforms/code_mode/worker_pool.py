"""Worker pool managing isolated worker processes for Code Mode."""
from __future__ import annotations

import asyncio
import inspect
import json
import logging
import multiprocessing as mp
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess
import time
from typing import Any, Dict, List, Optional

from nitrostack.transforms.code_mode.guest_evaluator import (
    get_quickjs_module,
    worker_process_main,
)
from nitrostack.transforms.code_mode.sandbox_interface import SandboxProtocol
from nitrostack.transforms.code_mode.types import CodeModeLimits, SandboxExecutionResult

logger = logging.getLogger(__name__)


class _WorkerSlot:
    """Manages an individual worker subprocess."""

    def __init__(self) -> None:
        self.process: Optional[BaseProcess] = None
        self.conn: Optional[Connection] = None
        self._start()

    def _start(self) -> None:
        parent_conn, child_conn = mp.Pipe(duplex=True)
        ctx = mp.get_context("spawn")
        p = ctx.Process(target=worker_process_main, args=(child_conn,), daemon=True)
        p.start()
        child_conn.close()
        self.process = p
        self.conn = parent_conn
        if parent_conn.poll(10.0):
            try:
                msg = parent_conn.recv()
                if msg.get("type") != "ready":
                    logger.warning("Unexpected worker initial message: %s", msg)
            except Exception as e:
                logger.warning("Error receiving worker ready signal: %s", e)
        else:
            logger.error("Worker process failed to become ready within 10s")

    def is_alive(self) -> bool:
        return (
            self.conn is not None
            and self.process is not None
            and self.process.is_alive()
        )

    def ensure_started(self) -> None:
        if not self.is_alive():
            self.recycle()

    def terminate(self) -> None:
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass
            self.conn = None
        if self.process:
            try:
                self.process.kill()
                self.process.join(timeout=1.0)
            except Exception:
                pass
            self.process = None

    def recycle(self) -> None:
        self.terminate()
        try:
            self._start()
        except Exception as exc:
            logger.error("Failed to start worker process during recycle: %s", exc)
            self.conn = None
            self.process = None


class WorkerPool(SandboxProtocol):
    """Maintains a pool of isolated worker processes with concurrency and recovery."""

    def __init__(self, limits: Optional[CodeModeLimits] = None) -> None:
        get_quickjs_module()  # Verify dependency is installed
        self.limits = limits or CodeModeLimits()
        self.pool_size = self.limits.worker_pool_size
        self._queue: asyncio.Queue[_WorkerSlot] = asyncio.Queue()
        self._all_slots: List[_WorkerSlot] = []
        self._disposed = False

        for _ in range(self.pool_size):
            slot = _WorkerSlot()
            self._all_slots.append(slot)
            self._queue.put_nowait(slot)

    async def run(
        self,
        code: str,
        tools_api: Any,
        limits: Optional[CodeModeLimits] = None,
    ) -> SandboxExecutionResult:
        if self._disposed:
            raise RuntimeError("Worker pool has been disposed.")

        effective_limits = limits or self.limits
        timeout_seconds = effective_limits.timeout_ms / 1000.0

        slot = await self._queue.get()
        if self._disposed:
            raise RuntimeError("Worker pool has been disposed.")

        slot.ensure_started()
        if slot.conn is None:
            raise RuntimeError("Worker process connection is unavailable.")

        start_time = time.perf_counter()

        try:
            slot.conn.send({
                "cmd": "run",
                "code": code,
                "limits": {
                    "timeout_ms": effective_limits.timeout_ms,
                    "memory_limit_mb": effective_limits.memory_limit_mb,
                    "max_tool_calls": effective_limits.max_tool_calls,
                    "allow_destructive": effective_limits.allow_destructive,
                },
            })

            # Process messages until run_result or timeout
            while True:
                # Poll connection in executor with remaining timeout
                elapsed = time.perf_counter() - start_time
                remaining = timeout_seconds - elapsed
                if remaining <= 0:
                    raise asyncio.TimeoutError()

                def check_poll(conn: Connection, timeout: float) -> bool:
                    return conn.poll(timeout)

                loop = asyncio.get_running_loop()
                has_msg = await asyncio.wait_for(
                    loop.run_in_executor(None, check_poll, slot.conn, remaining),
                    timeout=remaining + 0.5,
                )

                if not has_msg:
                    raise asyncio.TimeoutError()

                msg = slot.conn.recv()
                msg_type = msg.get("type")

                if msg_type == "tool_call":
                    tool_name = msg["name"]
                    args_json = msg.get("args_json", "{}")
                    try:
                        args = json.loads(args_json) if args_json else {}
                    except Exception:
                        args = {}

                    try:
                        elapsed = time.perf_counter() - start_time
                        remaining_tool_time = timeout_seconds - elapsed
                        if remaining_tool_time <= 0:
                            raise asyncio.TimeoutError()

                        async def _invoke_tool() -> Any:
                            if hasattr(tools_api, "call"):
                                call_fn = getattr(tools_api, "call")
                                res = call_fn(tool_name, args)
                                return await res if inspect.isawaitable(res) else res
                            elif callable(tools_api):
                                res = tools_api(tool_name, args)
                                return await res if inspect.isawaitable(res) else res
                            else:
                                raise RuntimeError("Tools API is not callable.")

                        val = await asyncio.wait_for(_invoke_tool(), timeout=remaining_tool_time)

                        if slot.conn is not None:
                            slot.conn.send({"status": "ok", "value": val})
                    except asyncio.TimeoutError:
                        raise
                    except Exception as exc:
                        if slot.conn is not None:
                            slot.conn.send({
                                "status": "error",
                                "error": str(exc),
                                "error_type": type(exc).__name__,
                            })

                elif msg_type == "run_result":
                    # Success or handled script error
                    return SandboxExecutionResult(
                        success=msg["success"],
                        value=msg["value"],
                        error=msg["error"],
                        logs=msg["logs"],
                        tool_call_count=msg["tool_call_count"],
                        duration_ms=msg["duration_ms"],
                        memory_used_mb=msg["memory_used_mb"],
                    )

        except asyncio.TimeoutError:
            # Kill worker process and replace it in the pool
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            try:
                slot.recycle()
            except Exception as e:
                logger.error("Error recycling worker slot after timeout: %s", e)
            return SandboxExecutionResult(
                success=False,
                error=f"TimeoutError: Script execution timed out after {effective_limits.timeout_ms}ms.",
                duration_ms=duration_ms,
            )
        except Exception as exc:
            # Worker crashed or unexpected error
            duration_ms = (time.perf_counter() - start_time) * 1000.0
            try:
                slot.recycle()
            except Exception as e:
                logger.error("Error recycling worker slot after error: %s", e)
            return SandboxExecutionResult(
                success=False,
                error=f"RuntimeError: Worker execution failed: {exc}",
                duration_ms=duration_ms,
            )
        finally:
            if not self._disposed:
                if not slot.is_alive():
                    try:
                        slot.recycle()
                    except Exception as e:
                        logger.error("Failed to recycle dead worker slot: %s", e)
                self._queue.put_nowait(slot)

    async def dispose(self) -> None:
        self._disposed = True
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        for slot in self._all_slots:
            slot.terminate()
        self._all_slots.clear()
