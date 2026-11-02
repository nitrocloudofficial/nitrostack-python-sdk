import time
import inspect
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Callable, Any, Dict, List, Tuple, TypedDict, Literal, Optional
from nitrostack.core.context import ExecutionContext

def copy_mcp_attributes(src: Any, dst: Any) -> None:
    """Helper to copy all _mcp_ attributes from src function to dst function."""
    for attr in dir(src):
        if attr.startswith("_mcp_"):
            setattr(dst, attr, getattr(src, attr))

def cache(ttl: int = 60):
    """
    Caches method outputs for a specific TTL (in seconds).
    Skips ExecutionContext parameters when generating the cache key.
    """
    def decorator(func: Callable):
        cache_store: Dict[Tuple[Any, ...], Tuple[Any, float]] = {}
        setattr(func, "_mcp_cache_ttl", ttl)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            # Construct a cache key by filtering out ExecutionContext and self
            filtered_args = []
            for arg in args:
                # Skip self if it has class attributes or is the instance, skip ExecutionContext
                if isinstance(arg, ExecutionContext) or type(arg).__name__ == "ExecutionContext":
                    continue
                filtered_args.append(arg)

            filtered_kwargs = {}
            for k, v in kwargs.items():
                if isinstance(v, ExecutionContext) or type(v).__name__ == "ExecutionContext":
                    continue
                filtered_kwargs[k] = v

            # Standardize key (args and kwargs)
            key = (tuple(filtered_args), frozenset(filtered_kwargs.items()))

            now = time.time()
            if key in cache_store:
                val, expiry = cache_store[key]
                if now < expiry:
                    return val

            result = await func(*args, **kwargs)
            cache_store[key] = (result, now + ttl)
            return result

        copy_mcp_attributes(func, wrapper)
        setattr(wrapper, "_mcp_cache_ttl", ttl)
        return wrapper
    return decorator

def rate_limit(max: int, window: int):
    """
    Rate limits method calls to max calls per window (in seconds).
    """
    def decorator(func: Callable):
        calls: List[float] = []

        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal calls
            now = time.time()
            # Filter timestamps in the window
            calls = [t for t in calls if now - t < window]
            if len(calls) >= max:
                raise ValueError(f"Rate limit exceeded. Maximum of {max} calls allowed every {window} seconds.")
            
            calls.append(now)
            return await func(*args, **kwargs)

        copy_mcp_attributes(func, wrapper)
        return wrapper
    return decorator


class TransformTelemetry(TypedDict):
    name: str
    type: str
    order: int
    details: Dict[str, Any]


class HealthCheckResult(TypedDict):
    name: str
    status: Literal["healthy", "unhealthy", "error", "timeout"]
    latencyMs: float
    message: Optional[str]


class ServerHealthPayload(TypedDict, total=False):
    status: Literal["healthy", "degraded", "unhealthy"]
    uptimeSeconds: int
    checks: List[HealthCheckResult]
    count: int
    transforms: List[TransformTelemetry]
    timestamp: str
    sdkVersion: str


class HealthCheckRegistry:
    # Maps name -> (unbound_func, class_type)
    _checks: Dict[str, Tuple[Callable, Any]] = {}
    # Maps name -> bound_callable
    _bound_checks: Dict[str, Tuple[Callable[[], bool], float]] = {}
    DEFAULT_TIMEOUT_SECONDS = 5.0
    SYNC_EXECUTOR_WORKERS = 4
    _sync_executor = ThreadPoolExecutor(
        max_workers=SYNC_EXECUTOR_WORKERS,
        thread_name_prefix="nitrostack-health",
    )
    # Bound submitted work as well as running threads. Timed-out functions retain
    # their slot until they actually return, preventing an unbounded work queue.
    _sync_slots = threading.BoundedSemaphore(SYNC_EXECUTOR_WORKERS)

    @classmethod
    def register(cls, name: str, func: Callable, class_type: Any = None) -> None:
        cls._checks[name] = (func, class_type)

    @classmethod
    def bind_instance(cls, name: str, func: Callable, instance: Any, timeout: float = 5.0) -> None:
        # Bind the method to the resolved instance
        import inspect
        if inspect.ismethod(func):
            cls._bound_checks[name] = (func, timeout)
        else:
            cls._bound_checks[name] = (lambda: func(instance), timeout)

    @classmethod
    def get_checks(cls) -> Dict[str, Callable[[], bool]]:
        return {
            name: value[0] if isinstance(value, tuple) else value
            for name, value in cls._bound_checks.items()
        }

    @classmethod
    async def run_all(cls) -> Dict[str, str]:
        """Run every registered health check and return {name: status}.

        Coroutine checks are awaited, sync checks are called directly, and a
        check that raises reports its own error without affecting the others.
        """
        detailed = await cls.run_detailed()
        return {
            result["name"]: (
                f'error: {result["message"]}' if result["status"] == "error"
                else "unhealthy" if result["status"] == "timeout"
                else result["status"]
            )
            for result in detailed
        }

    @classmethod
    async def run_detailed(cls) -> List[HealthCheckResult]:
        """Run all checks concurrently, bounding each by its own timeout."""
        async def run_one(name: str, check: Callable, timeout: float) -> HealthCheckResult:
            started = time.perf_counter()
            try:
                async def invoke():
                    if inspect.iscoroutinefunction(check):
                        return await check()
                    while not cls._sync_slots.acquire(blocking=False):
                        await asyncio.sleep(0.01)
                    try:
                        future = cls._sync_executor.submit(check)
                    except BaseException:
                        cls._sync_slots.release()
                        raise
                    future.add_done_callback(lambda _future: cls._sync_slots.release())
                    result = await asyncio.wrap_future(future)
                    return await result if inspect.isawaitable(result) else result

                outcome = await asyncio.wait_for(invoke(), timeout=timeout)
                status = "healthy" if outcome else "unhealthy"
                message = None if outcome else "Check returned false"
            except asyncio.TimeoutError:
                status, message = "timeout", f"Timed out after {timeout:g}s"
            except Exception as exc:  # noqa: BLE001 - one bad check must not hide the rest
                status, message = "error", str(exc)
            return {
                "name": name,
                "status": status,
                "latencyMs": round((time.perf_counter() - started) * 1000, 3),
                "message": message,
            }

        checks = [
            (name, value if isinstance(value, tuple) else (value, cls.DEFAULT_TIMEOUT_SECONDS))
            for name, value in cls._bound_checks.items()
        ]
        return list(await asyncio.gather(*(
            run_one(name, check, timeout) for name, (check, timeout) in checks
        )))

def health_check(name: str, timeout: float = HealthCheckRegistry.DEFAULT_TIMEOUT_SECONDS):
    """
    Decorator to mark a service or controller method as a health check.
    """
    if timeout <= 0:
        raise ValueError("health check timeout must be greater than zero")

    def decorator(func: Callable):
        func._mcp_health_check_name = name
        # We don't have the class type here yet, but we will register it.
        # It will be bound during discovery in the McpApplicationFactory.
        HealthCheckRegistry.register(name, func)
        func._mcp_health_check_timeout = timeout
        return func
    return decorator
