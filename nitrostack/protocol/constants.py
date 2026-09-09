"""Cross-cutting security and validation limits."""

# RFC 6890 CIMD resolver payload cap
MAX_CIMD_BYTES = 5120

# JSON Schema depth bounding for DoS protection (SEP-2106)
MAX_SCHEMA_DEPTH = 64

# Legacy session header that MUST NOT be emitted in stateless mode
LEGACY_SESSION_HEADER = "Mcp-Session-Id"
