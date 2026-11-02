"""Format legible error messages for models without leaking host paths or stack frames."""
from __future__ import annotations

import re

_HOST_PATH_RE = re.compile(
    r"(?:/[a-zA-Z0-9_\-\.]+)+|(?:[a-zA-Z]:\\[a-zA-Z0-9_\-\.\\]+)"
)

# Match Python traceback line patterns
_PY_TRACEBACK_LINE_RE = re.compile(
    r"^\s*File\s+\".*?\",\s+line\s+\d+.*$",
    re.MULTILINE
)


def format_legible_error(exc: BaseException | str) -> str:
    """Format an error message so it is readable (message and location) and leaks no host paths or stack frames."""
    raw = str(exc)

    # Clean up input markers
    raw = raw.replace("<eval>", "<script>").replace("<input>", "<script>")

    # Remove python traceback lines
    lines = []
    for line in raw.splitlines():
        if _PY_TRACEBACK_LINE_RE.match(line):
            continue
        if "Traceback (most recent call last):" in line:
            continue
        lines.append(line)

    cleaned = "\n".join(lines).strip()

    # Scrub any host filesystem paths that may remain
    def _path_replacer(match: re.Match) -> str:
        matched = match.group(0)
        if "<script>" in matched:
            return matched
        return "<sanitized-path>"

    cleaned = _HOST_PATH_RE.sub(_path_replacer, cleaned)
    return cleaned or "ScriptExecutionError: Unknown error occurred."
