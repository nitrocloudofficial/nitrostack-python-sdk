"""Cross-cutting security and validation limits (Doc 00 — defense in depth)."""

# RFC 6890 CIMD resolver payload cap (Doc 08; principle 4)
MAX_CIMD_BYTES = 5120

# JSON Schema depth bounding for DoS protection (SEP-2106; Doc 03)
MAX_SCHEMA_DEPTH = 64

# Legacy session header that MUST NOT be emitted in stateless mode (Doc 01)
LEGACY_SESSION_HEADER = "Mcp-Session-Id"
