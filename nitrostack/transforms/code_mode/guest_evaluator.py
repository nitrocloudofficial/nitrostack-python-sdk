from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, List, Optional
from multiprocessing.connection import Connection

from nitrostack.transforms.code_mode.legible_error import format_legible_error
from nitrostack.transforms.code_mode.types import CodeModeLimits, SandboxExecutionResult

logger = logging.getLogger(__name__)

JS_PRELUDE = """
const console = {
    log: (...args) => { __nitrostack_log('info', __formatArgs(args)); },
    info: (...args) => { __nitrostack_log('info', __formatArgs(args)); },
    warn: (...args) => { __nitrostack_log('warn', __formatArgs(args)); },
    error: (...args) => { __nitrostack_log('error', __formatArgs(args)); }
};

function __formatArgs(args) {
    return args.map(a => {
        if (typeof a === 'object' && a !== null) {
            try { return JSON.stringify(a); } catch(e) { return String(a); }
        }
        return String(a);
    }).join(' ');
}

const tools = new Proxy({
    call(name, args) {
        const payload = JSON.stringify(args || {});
        const resStr = __nitrostack_call_tool(String(name), payload);
        const parsed = JSON.parse(resStr);
        if (parsed && parsed.__nitrostack_error) {
            const err = new Error(parsed.__nitrostack_error);
            err.name = parsed.__nitrostack_error_type || 'Error';
            throw err;
        }
        return parsed.__nitrostack_value;
    }
}, {
    get(target, prop) {
        if (prop in target) return target[prop];
        return function(args) {
            return target.call(prop, args);
        };
    }
});

const call_tool = (name, args) => tools.call(name, args);
const call = (name, args) => tools.call(name, args);
"""


def get_quickjs_module():
    try:
        import quickjs
        return quickjs
    except ImportError as exc:
        raise RuntimeError(
            "The 'quickjs' package is required for Code Mode. "
            "Install it with: pip install 'nitrostack[code-mode]'"
        ) from exc


def worker_process_main(conn: Connection) -> None:
    """Main loop for isolated worker process."""
    quickjs = get_quickjs_module()
    conn.send({"type": "ready"})

    while True:
        try:
            if not conn.poll(None):
                break
            msg = conn.recv()
        except (EOFError, KeyboardInterrupt):
            break

        cmd = msg.get("cmd")
        if cmd == "shutdown":
            break

        if cmd == "run":
            code = msg["code"]
            limits_dict = msg["limits"]
            memory_limit_mb = limits_dict.get("memory_limit_mb", 100)

            ctx = quickjs.Context()
            memory_limit_bytes = int(memory_limit_mb * 1024 * 1024)
            ctx.set_memory_limit(memory_limit_bytes)

            logs: List[str] = []
            tool_call_count = 0

            def log_bridge(level: str, message: str) -> None:
                logs.append(f"[{level}] {message}")

            def tool_call_bridge(name: str, args_json: str) -> str:
                nonlocal tool_call_count
                tool_call_count += 1
                try:
                    conn.send({
                        "type": "tool_call",
                        "name": name,
                        "args_json": args_json,
                        "call_index": tool_call_count,
                    })
                    response = conn.recv()
                    if response.get("status") == "error":
                        return json.dumps({
                            "__nitrostack_error": response.get("error", "Tool execution error"),
                            "__nitrostack_error_type": response.get("error_type", "Error"),
                        })
                    return json.dumps({
                        "__nitrostack_value": response.get("value")
                    })
                except Exception as e:
                    return json.dumps({
                        "__nitrostack_error": format_legible_error(e),
                        "__nitrostack_error_type": type(e).__name__,
                    })

            ctx.add_callable("__nitrostack_log", log_bridge)
            ctx.add_callable("__nitrostack_call_tool", tool_call_bridge)

            # Evaluate prelude
            ctx.eval(JS_PRELUDE)

            start_time = time.perf_counter()
            success = False
            value = None
            error = None

            try:
                try:
                    raw_result = ctx.eval(code)
                except quickjs.JSException as exc:
                    if "return not in a function" in str(exc):
                        wrapped = "(function() {\n" + code + "\n})()"
                        raw_result = ctx.eval(wrapped)
                    else:
                        raise

                if hasattr(raw_result, "json"):
                    try:
                        value = json.loads(raw_result.json())
                    except Exception:
                        value = str(raw_result)
                else:
                    value = raw_result

                success = True
            except quickjs.JSException as exc:
                err_str = str(exc)
                if "out of memory" in err_str:
                    error = f"MemoryLimitExceededError: Script exceeded memory limit of {memory_limit_mb}MB."
                else:
                    error = format_legible_error(exc)
            except Exception as exc:
                error = format_legible_error(exc)
            finally:
                duration_ms = (time.perf_counter() - start_time) * 1000.0
                memory_used_mb = 0.0
                try:
                    mem_info = ctx.memory()
                    if isinstance(mem_info, dict):
                        used_bytes = mem_info.get("memory_used_size", 0)
                    else:
                        used_bytes = getattr(mem_info, "memory_used_size", 0)
                    memory_used_mb = used_bytes / (1024 * 1024)
                except Exception:
                    pass

                try:
                    ctx.gc()
                except Exception:
                    pass

            conn.send({
                "type": "run_result",
                "success": success,
                "value": value,
                "error": error,
                "logs": logs,
                "tool_call_count": tool_call_count,
                "duration_ms": duration_ms,
                "memory_used_mb": memory_used_mb,
            })
