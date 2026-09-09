"""Per-request context for official mcp 2.x (replaces 1.x ``request_ctx``)."""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Mapping, Optional

request_ctx: ContextVar[Any] = ContextVar("nitrostack_request_ctx", default=None)


@dataclass
class Experimental:
    """Task metadata previously hung off the 1.x experimental request slot."""

    task_metadata: Any = None


@dataclass
class RequestContext:
    """Test and in-process stand-in for the 1.x low-level request context."""

    request_id: Any = None
    correlation_id: Any = None
    meta: Any = None
    session: Any = None
    lifespan_context: Any = None
    experimental: Optional[Experimental] = None
    request: Any = None


class RequestParamsMeta:
    """Stand-in for 1.x ``types.RequestParams.Meta`` extra fields."""

    def __init__(self, __pydantic_extra__: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> None:
        extra = dict(__pydantic_extra__ or {})
        extra.update(kwargs)
        self.__pydantic_extra__ = extra
        for key, value in extra.items():
            setattr(self, key, value)

    @classmethod
    def model_validate(cls, data: Any) -> "RequestParamsMeta":
        if isinstance(data, cls):
            return data
        if data is None:
            return cls()
        if isinstance(data, Mapping):
            return cls(**dict(data))
        extra = getattr(data, "__pydantic_extra__", None)
        if isinstance(extra, Mapping):
            return cls(__pydantic_extra__=extra)
        return cls()


class ServerResult:
    """1.x ``ServerResult`` wrapper: in-process callers still read ``.root``."""

    def __init__(self, root: Any) -> None:
        self.root = root


def bind_request_ctx(ctx: Any):
    """Push ``ctx`` onto the request ContextVar and return the reset token."""
    return request_ctx.set(ctx)
