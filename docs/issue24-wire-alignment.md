# Issue #24: modern MCP wire alignment

PR #37 extends the official-transport foundation from PRs #34 and #36. The
official MCP implementation owns JSON-RPC parsing, dispatch, negotiation, SSE and
the modern method/name header checks. NitroStack supplies its handlers and era
policy; it does not recreate the deleted JSON-RPC sidecar.

## Behavior

| Area | Result |
|---|---|
| Ping | Available only in the legacy era through MCP's public `on_ping` hook. Auto and modern reject it. |
| Retired methods | Modern requests reject retired methods even when the application is configured as auto. Legacy-compatible auto requests retain their existing supported behavior. |
| Discovery | The shared builder supplies `_meta.serverInfo`, preserving cache hints and its top-level compatibility copy. MCP may omit legacy-only top-level fields when serializing the modern wire. |
| Ordinary results | Tool, resource and prompt handlers use native top-level `resultType`; existing metadata and specialized result types are preserved. |
| Capability error | `JsonRpcErrorCode.MISSING_REQUIRED_CLIENT_CAPABILITY` defines `-32021`, with its default message and public protocol export. NitroStack-specific live task-capability enforcement is deferred to the tasks issue. |
| Tasks extension | The application does not advertise the unimplemented 2026 tasks extension, including through custom discovery extensions. Existing legacy task functionality remains available. |

## Why a small HTTP header check is still necessary

MCP 2.2.0 selects its strict modern HTTP entry using `MCP-Protocol-Version`. On
that entry, MCP validates `Mcp-Method`, `Mcp-Name`, protocol metadata, duplicates
and unsupported versions. A missing version header instead selects the SDK's
legacy-compatible HTTP entry, so the strict path cannot enforce its own entry
condition.

The existing mount's `HeaderCompatMiddleware` therefore rejects a modern-era
POST with a missing or blank version header using HTTP 400 / error `-32020`.
It does not read the body, infer a method, replay input or dispatch a request.
The response uses a null ID because the JSON-RPC body was not inspected. Health
and other routes remain outside this mount-specific check.

Auto preserves genuine 2025 requests. If a request's body explicitly claims the
modern protocol but lacks the HTTP version header, the existing server middleware
rejects the inconsistency. Stdio has no HTTP headers and is exempt from HTTP-only
requirements. Version-header normalization retains duplicate entries so the
official validator can reject them.

The retirement middleware also evaluates the request's negotiated protocol, not
only the application's era. This prevents auto's legacy task handlers from
answering methods retired on a modern request. Handler registration still uses
MCP's public APIs.

## Result fields and compatibility

The SDK's models provide a native `result_type` field serialized as `resultType`.
The handlers use that field rather than inventing `_meta.resultType`. Model
updates retain application metadata and any already-selected result type.

Discovery's requested `_meta.serverInfo` is distinct from MCP 2.2.0's reserved
`io.modelcontextprotocol/serverInfo` metadata stamp on ordinary modern results.
Tests check the correct location for each. Existing MRTR and legacy task handling
is retained; this PR does not implement a new tasks or MRTR protocol.

## Removed machinery and replacement coverage

Removed code includes the unused sessionless initialize payload builder and
application/server aliases, the test-only modern method-contract table, and the
duplicate optional/required method/name validators. Initialization remains owned
by MCP.

| Previous coverage | Replacement |
|---|---|
| Direct checks of deleted dispatch/pipeline objects | PR #36's migrated HTTP probes retained through the dependency merge. |
| Required/optional method and name helper tests | HTTP requests with missing and mismatched headers for tools, resources and prompts in auto/modern. |
| Static method-contract membership assertion | Successful real HTTP operations covering the requested tool/resource/prompt surface. |
| Sessionless initialize helper inspection | Actual auto initialization response through the official transport. |
| Ping as an HTTP success probe | Tool listing as the success probe, plus explicit legacy success and modern/auto retirement tests for ping. |

Pure JSON-RPC parsing/error mapping, protocol-version consistency, CORS utilities,
SSE formatting and response-header utility tests remain. No new skips, xfail
markers or exclusions are used.

## Dependency merge details

The updated PR #34 contained the new custom CORS middleware and older versions of
the four migrated test modules. The conflict resolution retains the corrected
PR #36 tests and the single Starlette `CORSMiddleware`. Browser-defined parameter
headers are supported through Starlette's request-header mirroring, while allowed
origins remain governed by `MCP_CORS_ALLOWED_ORIGINS`.

CORS tests assert Starlette's HTTP 200 preflight response and inspect exposed
headers on actual responses. The dual HTTP/stdio test now includes the required
HTTP version header with its modern body metadata.

The stash was inspected selectively and remains intact. Generated bytecode,
scaffolded applications, OAuth client changes and spillover changes were not
ported into this implementation. See `pr37-review-verification.md` for exact
commit IDs, reproduction commands and validation results.
