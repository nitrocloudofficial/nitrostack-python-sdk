import time
import inspect
from collections import OrderedDict, deque
from functools import wraps
from typing import Callable, Any, Deque, Dict, Optional, Tuple
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from functools import wraps
from typing import Callable, Any, Dict, List, Tuple, TypedDict, Literal, Optional
from nitrostack.core.context import ExecutionContext
from nitrostack.core.errors import RateLimitExceededError

def copy_mcp_attributes(src: Any, dst: Any) -> None:
    """Helper to copy all _mcp_ attributes from src function to dst function."""
    for attr in dir(src):
        if attr.startswith("_mcp_"):
            setattr(dst, attr, getattr(src, attr))

def cache(ttl: int = 60, max_size: Optional[int] = 1024):
    """
    Caches method outputs for a specific TTL (in seconds).
    Skips ExecutionContext parameters when generating the cache key.
    Holds at most max_size entries, evicting expired entries first and then
    the least recently used. max_size=None disables the cap.
    The wrapped function exposes cache_clear() to empty the cache.
    """
    if ttl <= 0:
        raise ValueError(f"cache ttl must be > 0, got {ttl!r}.")
    if max_size is not None and (not isinstance(max_size, int) or isinstance(max_size, bool) or max_size < 1):
        raise ValueError(f"cache max_size must be a positive integer or None, got {max_size!r}.")

    def decorator(func: Callable):
        cache_store: "OrderedDict[Tuple[Any, ...], Tuple[Any, float]]" = OrderedDict()
        setattr(func, "_mcp_cache_ttl", ttl)

        @wraps(func)
        async def wrapper(*args, **kwargs):
            filtered_args = []
            for arg in args:
                if isinstance(arg, ExecutionContext) or type(arg).__name__ == "ExecutionContext":
                    continue
                filtered_args.append(arg)

            filtered_kwargs = {}
            for k, v in kwargs.items():
                if isinstance(v, ExecutionContext) or type(v).__name__ == "ExecutionContext":
                    continue
                filtered_kwargs[k] = v

            key = (tuple(filtered_args), frozenset(filtered_kwargs.items()))

            now = time.time()
            entry = cache_store.get(key)
            if entry is not None:
                val, expiry = entry
                if now < expiry:
                    cache_store.move_to_end(key)
                    return val
                del cache_store[key]

            result = await func(*args, **kwargs)
            cache_store[key] = (result, now + ttl)
            cache_store.move_to_end(key)

            if max_size is not None and len(cache_store) > max_size:
                for stale in [k for k, (_, exp) in cache_store.items() if exp <= now]:
                    del cache_store[stale]
                while len(cache_store) > max_size:
                    cache_store.popitem(last=False)
            return result

        copy_mcp_attributes(func, wrapper)
        setattr(wrapper, "_mcp_cache_ttl", ttl)
        wrapper.cache_clear = cache_store.clear
        return wrapper
    return decorator

def rate_limit(max: int, window: int, key: Optional[Callable[..., Any]] = None):
    """
    Rate limits method calls to max calls per window (in seconds).
    By default one counter is shared by all callers of the decorated function.
    Pass key=callable(*args, **kwargs) -> hashable to keep a separate counter
    per key, for example per user. Raises RateLimitExceededError (a ValueError)
    carrying retry_after_seconds. Counters are held in memory per process.
    """
    if not isinstance(max, int) or isinstance(max, bool) or max < 1:
        raise ValueError(f"rate_limit max must be an integer >= 1, got {max!r}.")
    if window <= 0:
        raise ValueError(f"rate_limit window must be > 0, got {window!r}.")

    
    def decorator(func: Callable):
        buckets: Dict[Any, Deque[float]] = {}
        last_sweep = time.time()

        @wraps(func)
        async def wrapper(*args, **kwargs):
            nonlocal last_sweep
            now = time.time()

            if now - last_sweep >= window:
                for k in [k for k, b in buckets.items() if not b or now - b[-1] >= window]:
                    del buckets[k]
                last_sweep = now

            bucket_key = key(*args, **kwargs) if key is not None else None
            bucket = buckets.setdefault(bucket_key, deque())
            while bucket and now - bucket[0] >= window:
                bucket.popleft()

            if len(bucket) >= max:
                retry_after = window - (now - bucket[0])
                raise RateLimitExceededError(
                    f"Rate limit exceeded. Maximum of {max} calls allowed every {window} seconds. "
                    f"Retry after {retry_after:.1f} seconds.",
                    retry_after_seconds=retry_after,
                )

            bucket.append(now)
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
