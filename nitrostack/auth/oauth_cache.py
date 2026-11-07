"""Token-result cache operations. Depends only on ``oauth_types``."""

import time
from typing import Dict, Optional, Tuple

from nitrostack.auth.oauth_types import TokenInfo

# token -> (introspection result, monotonic expiry)
TokenCacheStore = Dict[str, Tuple[TokenInfo, float]]


def cache_get(store: TokenCacheStore, token: str) -> Optional[TokenInfo]:
    cached = store.get(token)
    if cached is None:
        return None
    result, expires_at = cached
    if time.monotonic() >= expires_at:
        del store[token]
        return None
    return result


def cache_set(store: TokenCacheStore, token: str, result: TokenInfo, ttl_seconds: int) -> None:
    if ttl_seconds <= 0:
        return
    store[token] = (result, time.monotonic() + ttl_seconds)
