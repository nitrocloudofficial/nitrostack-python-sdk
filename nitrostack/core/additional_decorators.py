import time
from collections import OrderedDict, deque
from functools import wraps
from typing import Callable, Any, Deque, Dict, Optional, Tuple
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
    if max_size is not None and max_size < 1:
        raise ValueError("max_size must be a positive integer or None")

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


class HealthCheckRegistry:
    # Maps name -> (unbound_func, class_type)
    _checks: Dict[str, Tuple[Callable, Any]] = {}
    # Maps name -> bound_callable
    _bound_checks: Dict[str, Callable[[], bool]] = {}

    @classmethod
    def register(cls, name: str, func: Callable, class_type: Any = None) -> None:
        cls._checks[name] = (func, class_type)

    @classmethod
    def bind_instance(cls, name: str, func: Callable, instance: Any) -> None:
        # Bind the method to the resolved instance
        import inspect
        if inspect.ismethod(func):
            cls._bound_checks[name] = func
        else:
            cls._bound_checks[name] = lambda: func(instance)

    @classmethod
    def get_checks(cls) -> Dict[str, Callable[[], bool]]:
        return cls._bound_checks

    @classmethod
    def run_all(cls) -> Dict[str, str]:
        results = {}
        for name, check_fn in cls._bound_checks.items():
            try:
                # Handle both sync and async checks
                import inspect
                if inspect.iscoroutinefunction(check_fn):
                    # In a real environment, we'd await it, but let's handle it or run via loop
                    import asyncio
                    try:
                        loop = asyncio.get_event_loop()
                    except RuntimeError:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                    status = loop.run_until_complete(check_fn())
                else:
                    status = check_fn()
                results[name] = "healthy" if status else "unhealthy"
            except Exception as e:
                results[name] = f"error: {str(e)}"
        return results

def health_check(name: str):
    """
    Decorator to mark a service or controller method as a health check.
    """
    def decorator(func: Callable):
        func._mcp_health_check_name = name
        # We don't have the class type here yet, but we will register it.
        # It will be bound during discovery in the McpApplicationFactory.
        HealthCheckRegistry.register(name, func)
        return func
    return decorator
