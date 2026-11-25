"""Shared constants for the HTTP transport."""
from __future__ import annotations

import logging
import time

from nitrostack.transports.headers import CORS_ALLOW_HEADER_NAMES

logger = logging.getLogger("nitrostack.transports.http")

DEFAULT_ENDPOINT = "/mcp"

# Shared 2026 allow-headers. Session id is exposed only on the sessionful engine.
CORS_ALLOW_HEADERS = list(CORS_ALLOW_HEADER_NAMES)

# The Accept value `StreamableHTTPServerTransport` requires: it needs
# `application/json` on POST and `text/event-stream` on both POST and GET.
MCP_ACCEPT = "application/json, text/event-stream"

_PROCESS_START = time.monotonic()