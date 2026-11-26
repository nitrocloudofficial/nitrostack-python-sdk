"""ExecutionContext.input mirrors TS #373 (pre-pipe tool arguments)."""

from __future__ import annotations

import asyncio
import os
import sys
from typing import List

from pydantic import BaseModel, Field

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from nitrostack import DIContainer, ExecutionContext
from nitrostack.core.pipeline import PipeMetadata, run_pipeline

seen: dict = {}


class FlightInput(BaseModel):
    from_: str = Field(alias="from")
    to: str


class CityInput(BaseModel):
    city: str


class RecordingGuard:
    async def can_activate(self, context: ExecutionContext) -> bool:
        seen["guard"] = dict(context.input or {})
        return True


class RecordingMiddleware:
    async def use(self, context: ExecutionContext, next_fn):
        seen["middleware"] = dict(context.input or {})
        return await next_fn()


class RecordingInterceptor:
    async def intercept(self, context: ExecutionContext, next_fn):
        seen["interceptor"] = dict(context.input or {})
        return await next_fn()


class UppercasePipe:
    async def transform(self, value, metadata: PipeMetadata):
        if isinstance(value, CityInput):
            return CityInput(city=value.city.upper())
        return value


def _register(cls, instance) -> None:
    DIContainer.get_instance().register_value(cls, instance)


def setup_function() -> None:
    DIContainer.reset()
    seen.clear()


def teardown_function() -> None:
    DIContainer.reset()


def test_context_input_visible_through_pipeline():
    _register(RecordingGuard, RecordingGuard())
    _register(RecordingMiddleware, RecordingMiddleware())
    _register(RecordingInterceptor, RecordingInterceptor())

    async def handler(_self, input: FlightInput, context: ExecutionContext):
        seen["handler"] = dict(context.input or {})
        return {"ok": True}

    args_dict = {"from": "SFO", "to": "JFK"}
    ctx = ExecutionContext(request_id="r1")
    asyncio.run(
        run_pipeline(
            handler=handler,
            handler_instance=object(),
            args=(FlightInput.model_validate(args_dict), ctx),
            kwargs={},
            context=ctx,
            guards=[RecordingGuard],
            middleware=[RecordingMiddleware],
            interceptors=[RecordingInterceptor],
            pipes=[],
            filters=[],
            param_type=FlightInput,
        )
    )
    assert seen["guard"] == args_dict
    assert seen["middleware"] == args_dict
    assert seen["interceptor"] == args_dict
    assert seen["handler"] == args_dict


def test_context_input_unchanged_when_pipes_transform_handler_input():
    _register(UppercasePipe, UppercasePipe())
    captured: List[dict] = []

    async def handler(_self, input: CityInput, context: ExecutionContext):
        captured.append({"handler": input.model_dump(), "context": dict(context.input or {})})
        return {}

    ctx = ExecutionContext(request_id="r2")
    asyncio.run(
        run_pipeline(
            handler=handler,
            handler_instance=object(),
            args=(CityInput(city="paris"), ctx),
            kwargs={},
            context=ctx,
            guards=[],
            middleware=[],
            interceptors=[],
            pipes=[UppercasePipe],
            filters=[],
            param_type=CityInput,
        )
    )
    assert captured[0]["handler"] == {"city": "PARIS"}
    assert captured[0]["context"] == {"city": "paris"}