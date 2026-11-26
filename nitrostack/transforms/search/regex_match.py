"""Run a user-supplied regex with a hard time limit, without blocking the event loop.

Python's `re` cannot be interrupted, so the match runs in a short-lived child
interpreter that is killed on timeout. The child imports only the standard library.
"""
from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Optional, Sequence, Set

_CHILD = r"""
import json, re, sys
req = json.load(sys.stdin)
try:
    rx = re.compile(req["pattern"], re.IGNORECASE)
except re.error as exc:
    print(json.dumps({"status": "invalid", "detail": str(exc)}))
    sys.exit(0)
hits = [i for i, t in enumerate(req["texts"]) if rx.search(t)]
print(json.dumps({"status": "ok", "hits": hits}))
"""


@dataclass
class RegexOutcome:
    hits: Optional[Set[int]]  # None means "could not use the regex"
    reason: Optional[str] = None  # why, when hits is None


async def run_regex(pattern: str, texts: Sequence[str], timeout: float) -> RegexOutcome:
    payload = json.dumps({"pattern": pattern, "texts": list(texts)}).encode("utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-I", "-c", _CHILD,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except (NotImplementedError, OSError) as exc:
        return RegexOutcome(None, f"regex worker could not start ({type(exc).__name__})")

    try:
        out, _ = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except asyncio.TimeoutError:
        return RegexOutcome(None, f"timed out after {timeout:g}s")
    finally:
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    try:
        data = json.loads(out.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return RegexOutcome(None, "regex worker returned no result")
    if data.get("status") == "invalid":
        return RegexOutcome(None, f"invalid pattern ({data.get('detail', 'syntax error')})")
    return RegexOutcome(set(data.get("hits", [])))