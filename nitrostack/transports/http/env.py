"""Environment variable helpers for the HTTP transport."""
from __future__ import annotations

import os
from typing import List


def _env_list(name: str) -> List[str]:
    raw = os.environ.get(name)
    if not raw:
        return []
    return [item.strip() for item in raw.split(",") if item.strip()]


def _env_flag(name: str) -> bool:
    return (os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}