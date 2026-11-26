"""Environment variable helpers for the HTTP transport."""
from __future__ import annotations

import os
import re
from typing import List, Union

DEFAULT_JSON_BODY_LIMIT = "1mb"


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def parse_json_body_limit(value: Union[str, int]) -> int:
    if isinstance(value, int):
        return value
    text = str(value).strip().lower().replace(" ", "")
    match = re.fullmatch(r"(\d+)(b|kb|mb)?", text)
    if not match:
        raise ValueError(f"Invalid JSON body limit: {value!r}")
    amount = int(match.group(1))
    unit = match.group(2) or "b"
    if unit == "kb":
        return amount * 1024
    if unit == "mb":
        return amount * 1024 * 1024
    return amount


def resolve_json_body_limit(config_value: Union[str, int, None] = None) -> int:
    """Config wins, then ``NITROSTACK_JSON_BODY_LIMIT`` / ``MCP_JSON_BODY_LIMIT``, else 1mb."""
    env = os.environ.get("NITROSTACK_JSON_BODY_LIMIT") or os.environ.get("MCP_JSON_BODY_LIMIT")
    raw = config_value if config_value is not None else (env or DEFAULT_JSON_BODY_LIMIT)
    return parse_json_body_limit(raw)