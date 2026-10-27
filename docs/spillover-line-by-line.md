# Issue #67: line-by-line implementation walkthrough

This document explains every added or modified Python line, including tests and exports. Existing untouched lines are omitted. Line numbers refer to the final files in this working tree. Blank lines and closing punctuation are identified too, so there are no unexplained gaps inside an added block.

## Read the feature in this order

1. **Storage:** the protocol defines what a backend must do; RAM and filesystem classes implement it.
2. **Interceptor:** execute a tool, serialize its result, compare its byte size, save only oversized results, and return a small typed wrapper.
3. **Application integration:** register resource prefixes, capture trusted caller identity, convert the wrapper to MCP, and serve authorized resource reads.
4. **Tests:** exercise storage contracts, preview size, real registered MCP handlers, authentication/session isolation, expiry, tasks, and filesystem rejection.

## Key ideas before reading individual lines

- **UTF-8 bytes versus characters:** `é` is one character but two bytes. Limits use the serialized byte length, so multibyte text cannot bypass them.
- **Threshold versus maximum:** 64 KiB is the default point at which a result spills; 10 MiB is the default maximum one stored payload may occupy. The 100 MiB total cap bounds each store.
- **TTL:** a five-minute default lifetime becomes an absolute timestamp. Reads do not refresh it. Expired entries disappear during operations, and file entries are also swept when reopening the store.
- **LRU:** successful memory reads move entries to the end of an OrderedDict. Eviction removes the first remaining key, which is the least recently used.
- **Owner:** a digest of verified identity or a server-created session key. The URI is a lookup address, not proof of authorization. Another caller receives not-found even if they know it.
- **Base64:** makes payload bytes fit into a JSON file without losing information. It is not encryption. File-store roots must be private to the server account and owned by one instance/process.
- **Async and threads:** the public store API is async. RAM operations are short synchronous critical sections; filesystem operations are moved to worker threads. A thread lock protects accounting and atomicity.
- **Typed wrapper:** SpilloverResult is internal and cannot be confused with an ordinary user dictionary. The MCP response contains a preview, URI, byte size, and a resource link, never the original oversized data.
- **Lifecycle:** memory data disappears on restart; unexpired disk data survives reopening. Anonymous ownership ends with its live session, while an authenticated principal can read after reconnecting.
- **Limits of v1:** resources return complete data. There are no partial reads, external cloud storage adapters, or shared file-root coordination across processes.

## `nitrostack/__init__.py`

Explains 17 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 3:** `from nitrostack.core.interceptors import (` — Import the named APIs from `nitrostack.core.interceptors` so this file can use the shared SDK, library, or test implementation.
- **Line 4:** `DataSpilloverInterceptor,` — Import or re-export `DataSpilloverInterceptor` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 5:** `FileSpilloverStore,` — Import or re-export `FileSpilloverStore` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 6:** `MemorySpilloverStore,` — Import or re-export `MemorySpilloverStore` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 7:** `PayloadTooLargeError,` — Import or re-export `PayloadTooLargeError` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 8:** `SpilloverEntry,` — Import or re-export `SpilloverEntry` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 9:** `SpilloverStore,` — Import or re-export `SpilloverStore` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 10:** `generate_preview,` — Import or re-export `generate_preview` from `nitrostack.core.interceptors` as part of the import block above.
- **Line 11:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 12:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 152:** `"DataSpilloverInterceptor",` — List `"DataSpilloverInterceptor"` as a supported public export so wildcard imports and API discovery include it.
- **Line 153:** `"FileSpilloverStore",` — List `"FileSpilloverStore"` as a supported public export so wildcard imports and API discovery include it.
- **Line 154:** `"MemorySpilloverStore",` — List `"MemorySpilloverStore"` as a supported public export so wildcard imports and API discovery include it.
- **Line 155:** `"PayloadTooLargeError",` — List `"PayloadTooLargeError"` as a supported public export so wildcard imports and API discovery include it.
- **Line 156:** `"SpilloverEntry",` — List `"SpilloverEntry"` as a supported public export so wildcard imports and API discovery include it.
- **Line 157:** `"SpilloverStore",` — List `"SpilloverStore"` as a supported public export so wildcard imports and API discovery include it.
- **Line 158:** `"generate_preview",` — List `"generate_preview"` as a supported public export so wildcard imports and API discovery include it.

## `nitrostack/core/app.py`

Explains 61 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 43:** `from nitrostack.core.interceptors.data_spillover import (` — Import the named APIs from `nitrostack.core.interceptors.data_spillover` so this file can use the shared SDK, library, or test implementation.
- **Line 44:** `DataSpilloverInterceptor, SPILLOVER_SCHEMA, SpilloverRegistry, SpilloverResult,` — Import or re-export `DataSpilloverInterceptor, SPILLOVER_SCHEMA, SpilloverRegistry, SpilloverResult` from `nitrostack.core.interceptors.data_spillover` as part of the import block above.
- **Line 45:** `bind_spillover_context, caller_owner,` — Import or re-export `bind_spillover_context, caller_owner` from `nitrostack.core.interceptors.data_spillover` as part of the import block above.
- **Line 46:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
### `McpApplication.__init__`

These lines connect this operation to the storage and resource flow described above.

- **Line 605:** `self._spillover = SpilloverRegistry()` — Set `self._spillover` to the application-owned registry shared by its tool and resource handlers: call `SpilloverRegistry` with the arguments shown to perform the current operation.
### `McpApplication._bootstrap`

This addition discovers opted-in tool interceptors after providers are ready and registers their stores before MCP handlers are exposed.

- **Line 677:** `for entry in self._tools.values():` — Iterate `entry` through `self._tools.values()`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 678:** `for candidate in getattr(entry.method, "_mcp_interceptors", []):` — Iterate `candidate` through `getattr(entry.method, '_mcp_interceptors', [])`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 679:** `interceptor = container.resolve(candidate) if isinstance(candidate, type) else candidate` — Set `interceptor`: select `container.resolve(candidate)` when `isinstance(candidate, type)` is true; otherwise select `candidate`.
- **Line 680:** `if isinstance(interceptor, DataSpilloverInterceptor):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 681:** `self._spillover.register(interceptor)` — Bind this interceptor's uri prefix to its configured store in the application-owned resource registry.
### `McpApplication._build_tool_definition`

This addition allows a decorated tool to advertise its original output shape or the spillover envelope. Root-level definitions are preserved so existing schema references keep resolving.

- **Line 1127:** `if self._has_spillover(entry.method):` — Check check whether this tool has the spillover interceptor before advertising an additional valid result shape. The indented body handles this case; other results continue past it.
- **Line 1128:** `output_schema = {` — Set `output_schema`: evaluate `{'type': 'object', 'anyOf': [output_schema, SPILLOVER_SCHEMA], **({'$defs': output_schema['$defs']} if '$defs' in output_schema else {})}`.
- **Line 1129:** `"type": "object", "anyOf": [output_schema, SPILLOVER_SCHEMA],` — Continue building `output_schema` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 1130:** `**({"$defs": output_schema["$defs"]} if "$defs" in output_schema else {}),` — Continue building `output_schema` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 1131:** `}` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
### `McpApplication._has_spillover`

Inspect the interceptors attached to a tool, resolving class-based registrations through the existing dependency-injection container. This determines whether the advertised output schema must also allow the spillover envelope.

- **Line 1176:** `def _has_spillover(self, method: Callable) -> bool:` — Define this callable. Inspect the interceptors attached to a tool, resolving class-based registrations through the existing dependency-injection container. This determines whether the advertised output schema must also allow the spillover envelope.
- **Line 1177:** `container = DIContainer.get_instance()` — Set `container`: call `DIContainer.get_instance` with the arguments shown to perform the current operation.
- **Line 1178:** `return any(` — Return from this operation: call `any` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 1179:** `isinstance(container.resolve(item) if isinstance(item, type) else item,` — Return from this operation: call `any` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 1180:** `DataSpilloverInterceptor)` — Return from this operation: call `any` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 1181:** `for item in getattr(method, "_mcp_interceptors", [])` — Return from this operation: call `any` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 1182:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 1183:** `(blank)` — Blank separator for readability; it performs no operation.
### `McpApplication._bind_spillover`

Prepare caller information only when this application has spillover routes. Reuse the SDK verified JWT context, or introspect an opaque token through an already configured OAuthService, then bind anonymous-session and failed-authentication state.

- **Line 1184:** `async def _bind_spillover(self, context: ExecutionContext, rc: Any) -> None:` — Define an asynchronous operation that callers await. Prepare caller information only when this application has spillover routes. Reuse the SDK verified JWT context, or introspect an opaque token through an already configured OAuthService, then bind anonymous-session and failed-authentication state.
- **Line 1185:** `if not self._spillover.routes:` — Check evaluate `not self._spillover.routes`. The indented body handles this case; other results continue past it.
- **Line 1186:** `return` — Return from this operation: evaluate `None`. No following statements in this call execute.
- **Line 1187:** `# OAuth guards may verify opaque tokens as well as the shared JWT path.` — Comment explaining the intent of the following operation; Python does not execute it.
- **Line 1188:** `from nitrostack.auth.request import authorization_token_from_request, auth_context_from_payload` — Import the named APIs from `nitrostack.auth.request` so this file can use the shared SDK, library, or test implementation.
- **Line 1189:** `from nitrostack.auth.oauth import OAuthService` — Import the named APIs from `nitrostack.auth.oauth` so this file can use the shared SDK, library, or test implementation.
- **Line 1190:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 1191:** `token = authorization_token_from_request(rc)` — Set `token`: extract a bearer token using the SDK's established header/envelope precedence without trusting unsigned identity fields.
- **Line 1192:** `container = DIContainer.get_instance()` — Set `container`: call `DIContainer.get_instance` with the arguments shown to perform the current operation.
- **Line 1193:** `if context.auth is None and token and container.has_value(OAuthService):` — Check evaluate `context.auth is None and token and container.has_value(OAuthService)`. The indented body handles this case; other results continue past it.
- **Line 1194:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 1195:** `payload = await container.resolve(OAuthService).introspect_token(token)` — Set `payload` to the complete serialized result as immutable bytes: await ask the configured OAuth service to verify the opaque token and return claims.
- **Line 1196:** `if payload.get("active"):` — Check look up the requested entry under the supplied ownership constraint. The indented body handles this case; other results continue past it.
- **Line 1197:** `context.auth = auth_context_from_payload(payload)` — Set `context.auth`: convert verified JWT or introspection claims into the common AuthContext used by tool and resource handlers.
- **Line 1198:** `except Exception:` — Handle only the exception types shown; the following body translates the failure or applies the documented fallback.
- **Line 1199:** `pass  # Invalid credentials remain denied by caller_owner.` — Perform no operation in this intentionally empty class or handled-error branch.
- **Line 1200:** `bind_spillover_context(context, rc, self._spillover)` — Bind trusted session information and the invalid-token marker onto this execution context.
- **Line 1201:** `(blank)` — Blank separator for readability; it performs no operation.
### `McpApplication._pipeline_stages`

These lines connect this operation to the storage and resource flow described above.

- **Line 1202:** `def _pipeline_stages(self, method: Callable) -> Tuple[List[Any], List[Any], List[Any], List[Any], List[Any]]:` — Define this callable. Its body below carries out the surrounding operation; this line defines it without running it.
### `McpApplication._to_call_tool_result`

The new early return converts a spillover wrapper directly to MCP, before ordinary result and widget transformation logic can reinterpret it.

- **Line 1217:** `if isinstance(result, SpilloverResult):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 1218:** `return result.to_mcp()` — Return from this operation: convert only the internal spillover wrapper into the small MCP envelope and resource link, bypassing ordinary widget data handling. No following statements in this call execute.
### `McpApplication._call_tool.background_execution`

These lines connect this operation to the storage and resource flow described above.

- **Line 1417:** `await self._bind_spillover(task_ctx, rc)` — Await resolve configured oauth identity when necessary and bind the trusted session/invalid-auth marker for spillover access checks.
### `McpApplication._call_tool`

The added bindings capture trusted caller/session information for ordinary calls and background tasks before their interceptors execute.

- **Line 1472:** `await self._bind_spillover(ctx, rc)` — Await resolve configured oauth identity when necessary and bind the trusted session/invalid-auth marker for spillover access checks.
### `McpApplication._read_resource`

The added route dispatch resolves spillover URIs first, rebuilds verified caller information, checks ownership through the store, and returns the exact saved bytes as text with their MIME type. Missing, invalid, expired, and unauthorized entries become ResourceNotFoundError.

- **Line 1505:** `for prefix, store in self._spillover.routes.items():` — Iterate `(prefix, store)` through `self._spillover.routes.items()`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 1506:** `if uri.startswith(prefix):` — Check call `uri.startswith` with the arguments shown to perform the current operation. The indented body handles this case; other results continue past it.
- **Line 1507:** `ctx = ExecutionContext(request_id=str(uuid.uuid4()))` — Set `ctx`: construct a fresh per-call context; its request ID identifies the operation while identity is populated separately from trusted request sources.
- **Line 1508:** `rc = request_ctx.get(None)` — Set `rc`: read the current request from the ContextVar, using None if no request is bound; this does not read a stored payload.
- **Line 1509:** `_apply_request_envelope(ctx, rc)` — Populate the execution context with verified jwt identity and existing mcp request metadata through the sdk's shared request binding.
- **Line 1510:** `await self._bind_spillover(ctx, rc)` — Await resolve configured oauth identity when necessary and bind the trusted session/invalid-auth marker for spillover access checks.
- **Line 1511:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 1512:** `entry = await store.get(uri[len(prefix):], owner=caller_owner(ctx))` — Set `entry`: await look up the requested entry under the supplied ownership constraint.
- **Line 1513:** `except (PermissionError, ValueError):` — Handle only the exception types shown; the following body translates the failure or applies the documented fallback.
- **Line 1514:** `raise ResourceNotFoundError(uri) from None` — Stop this operation and use the existing SDK not-found error without revealing whether a different owner has the requested data. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 1515:** `if entry is None:` — Check evaluate `entry is None`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 1516:** `raise ResourceNotFoundError(uri)` — Stop this operation and use the existing SDK not-found error without revealing whether a different owner has the requested data. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 1517:** `return [ReadResourceContents(` — Return from this operation: evaluate `[ReadResourceContents(content=entry.payload.decode('utf-8'), mime_type=entry.mime_type)]`. No following statements in this call execute.
- **Line 1518:** `content=entry.payload.decode("utf-8"), mime_type=entry.mime_type,` — Return from this operation: evaluate `[ReadResourceContents(content=entry.payload.decode('utf-8'), mime_type=entry.mime_type)]`. No following statements in this call execute.
- **Line 1519:** `)]` — Return from this operation: evaluate `[ReadResourceContents(content=entry.payload.decode('utf-8'), mime_type=entry.mime_type)]`. No following statements in this call execute.

## `nitrostack/core/context.py`

Explains 2 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `ExecutionContext`

These lines connect this operation to the storage and resource flow described above.

- **Line 200:** `spillover_session_key: Optional[str] = field(default=None, repr=False)` — Set `spillover_session_key` to the private session identity carried into the interceptor: declare a dataclass field with the displayed default; repr=False keeps private session/auth markers out of generated representations.
- **Line 201:** `spillover_invalid_auth: bool = field(default=False, repr=False)` — Set `spillover_invalid_auth` to the marker preventing invalid credentials from falling back to anonymous access: declare a dataclass field with the displayed default; repr=False keeps private session/auth markers out of generated representations.

## `nitrostack/core/errors.py`

Explains 4 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `PayloadTooLargeError`

A distinct ValueError subtype for the individual-payload limit. Applications can catch this specific condition separately from a full store or invalid configuration.

- **Line 4:** `class PayloadTooLargeError(ValueError):` — Define this class. A distinct ValueError subtype for the individual-payload limit. Applications can catch this specific condition separately from a full store or invalid configuration.
- **Line 5:** `"""A serialized tool result exceeds the spillover store's per-result limit."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 6:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 7:** `(blank)` — Blank separator for readability; it performs no operation.

## `nitrostack/core/interceptors/__init__.py`

Explains 15 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 1:** `"""Built-in tool interceptors and spillover storage adapters."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 2:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 3:** `from .data_spillover import DataSpilloverInterceptor, generate_preview` — Import the named APIs from `data_spillover` so this file can use the shared SDK, library, or test implementation.
- **Line 4:** `from .spillover import (` — Import the named APIs from `spillover` so this file can use the shared SDK, library, or test implementation.
- **Line 5:** `FileSpilloverStore,` — Import or re-export `FileSpilloverStore` from `spillover` as part of the import block above.
- **Line 6:** `MemorySpilloverStore,` — Import or re-export `MemorySpilloverStore` from `spillover` as part of the import block above.
- **Line 7:** `PayloadTooLargeError,` — Import or re-export `PayloadTooLargeError` from `spillover` as part of the import block above.
- **Line 8:** `SpilloverEntry,` — Import or re-export `SpilloverEntry` from `spillover` as part of the import block above.
- **Line 9:** `SpilloverStore,` — Import or re-export `SpilloverStore` from `spillover` as part of the import block above.
- **Line 10:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 11:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 12:** `__all__ = [` — Set `__all__`: evaluate `['DataSpilloverInterceptor', 'FileSpilloverStore', 'MemorySpilloverStore', 'PayloadTooLargeError', 'SpilloverEntry', 'SpilloverStore', 'generate_preview']`.
- **Line 13:** `"DataSpilloverInterceptor", "FileSpilloverStore", "MemorySpilloverStore",` — List `"DataSpilloverInterceptor", "FileSpilloverStore", "MemorySpilloverStore"` as a supported public export so wildcard imports and API discovery include it.
- **Line 14:** `"PayloadTooLargeError", "SpilloverEntry", "SpilloverStore", "generate_preview",` — List `"PayloadTooLargeError", "SpilloverEntry", "SpilloverStore", "generate_preview"` as a supported public export so wildcard imports and API discovery include it.
- **Line 15:** `]` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.

## `nitrostack/core/interceptors/data_spillover.py`

Explains 168 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 1:** `"""Tool-result spillover, bounded previews, and trusted caller ownership."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 2:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 3:** `from __future__ import annotations` — Import the named APIs from `__future__` so this file can use the shared SDK, library, or test implementation.
- **Line 4:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 5:** `import hashlib` — Import hashlib for the operations described below; importing binds the module names without calling their functions.
- **Line 6:** `import json` — Import json for the operations described below; importing binds the module names without calling their functions.
- **Line 7:** `import uuid` — Import uuid for the operations described below; importing binds the module names without calling their functions.
- **Line 8:** `import weakref` — Import weakref for the operations described below; importing binds the module names without calling their functions.
- **Line 9:** `from dataclasses import dataclass` — Import the named APIs from `dataclasses` so this file can use the shared SDK, library, or test implementation.
- **Line 10:** `from itertools import islice` — Import the named APIs from `itertools` so this file can use the shared SDK, library, or test implementation.
- **Line 11:** `from typing import Any` — Import the named APIs from `typing` so this file can use the shared SDK, library, or test implementation.
- **Line 12:** `from urllib.parse import urlsplit` — Import the named APIs from `urllib.parse` so this file can use the shared SDK, library, or test implementation.
- **Line 13:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 14:** `import mcp.types as types` — Import mcp.types for the operations described below; importing binds the module names without calling their functions.
- **Line 15:** `from pydantic import BaseModel` — Import the named APIs from `pydantic` so this file can use the shared SDK, library, or test implementation.
- **Line 16:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 17:** `from nitrostack.auth.request import authorization_token_from_request, tenant_from_claims` — Import the named APIs from `nitrostack.auth.request` so this file can use the shared SDK, library, or test implementation.
- **Line 18:** `from nitrostack.core.context import ExecutionContext` — Import the named APIs from `nitrostack.core.context` so this file can use the shared SDK, library, or test implementation.
- **Line 19:** `from nitrostack.protocol.mrtr import InputRequiredResult` — Import the named APIs from `nitrostack.protocol.mrtr` so this file can use the shared SDK, library, or test implementation.
- **Line 20:** `from nitrostack.core.interceptors.spillover import MemorySpilloverStore, SpilloverStore` — Import the named APIs from `nitrostack.core.interceptors.spillover` so this file can use the shared SDK, library, or test implementation.
- **Line 21:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 22:** `(blank)` — Blank separator for readability; it performs no operation.
### `generate_preview`

Build a small description of the original result: type, total count, and either five head elements or a string prefix. If nested values make that description too large, replace them with shortened text and repeatedly halve it until the serialized UTF-8 preview fits its budget.

- **Line 23:** `def generate_preview(value: Any, *, max_bytes: int = 2048) -> dict:` — Define this callable. Build a small description of the original result: type, total count, and either five head elements or a string prefix. If nested values make that description too large, replace them with shortened text and repeatedly halve it until the serialized UTF-8 preview fits its budget.
- **Line 24:** `"""Include head items and counts, shrinking even deeply nested values to fit."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 25:** `if max_bytes < 128:` — Check evaluate `max_bytes < 128`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 26:** `raise ValueError("Preview budget must be at least 128 bytes")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 27:** `if isinstance(value, list):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 28:** `preview = {"type": "list", "count": len(value), "head": value[:5], "truncated": len(value) > 5}` — Set `preview`: evaluate `{'type': 'list', 'count': len(value), 'head': value[:5], 'truncated': len(value) > 5}`.
- **Line 29:** `elif isinstance(value, dict):` — Test this next case only if previous branches did not match; the condition shown selects the appropriate handling.
- **Line 30:** `preview = {"type": "dict", "count": len(value), "head": dict(islice(value.items(), 5)),` — Set `preview`: evaluate `{'type': 'dict', 'count': len(value), 'head': dict(islice(value.items(), 5)), 'truncated': len(value) > 5}`.
- **Line 31:** `"truncated": len(value) > 5}` — Continue building `preview` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 32:** `else:` — Enter the alternative path only when the preceding condition did not match.
- **Line 33:** `text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)` — Set `text`: select `value` when `isinstance(value, str)` is true; otherwise select `json.dumps(value, ensure_ascii=False, default=str)`.
- **Line 34:** `preview = {"type": "string", "count": len(text), "text": text[:512], "truncated": len(text) > 512}` — Set `preview`: evaluate `{'type': 'string', 'count': len(text), 'text': text[:512], 'truncated': len(text) > 512}`.
- **Line 35:** `encoded = json.dumps(preview, ensure_ascii=False, default=str).encode("utf-8")` — Set `encoded`: convert text into the selected byte encoding, so storage and limits operate on bytes.
- **Line 36:** `if len(encoded) > max_bytes:` — Check evaluate `len(encoded) > max_bytes`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 37:** `text = json.dumps(preview.get("head", preview.get("text")), ensure_ascii=False, default=str)` — Set `text`: serialize the value to JSON text; ensure_ascii=False preserves Unicode and UTF-8 encoding determines the actual byte count.
- **Line 38:** `preview.pop("head", None)` — Remove the named mapping entry and return it when needed.
- **Line 39:** `preview["text"] = text[:512]` — Set `preview['text']`: evaluate `text[:512]`.
- **Line 40:** `preview["truncated"] = True` — Set `preview['truncated']`: use the literal `True`.
- **Line 41:** `while len(json.dumps(preview, ensure_ascii=False, default=str).encode("utf-8")) > max_bytes:` — Repeat the indented operation while `len(json.dumps(preview, ensure_ascii=False, default=str).encode('utf-8')) > max_bytes` remains true; each iteration reduces the preview or frees capacity so the loop can finish.
- **Line 42:** `preview["text"] = preview["text"][:len(preview["text"]) // 2]` — Set `preview['text']`: evaluate `preview['text'][:len(preview['text']) // 2]`.
- **Line 43:** `return preview` — Return from this operation: evaluate `preview`. No following statements in this call execute.
- **Line 44:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 45:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverResult`

This internal wrapper distinguishes an actual spillover response from an ordinary dictionary returned by a tool. That distinction lets the application bypass widget data transformation and produce the correct MCP resource link.

- **Line 46:** `@dataclass(frozen=True)` — Generate an immutable data container: field initialization and comparisons are provided, while frozen=True prevents field reassignment.
- **Line 47:** `class SpilloverResult:` — Define this class. This internal wrapper distinguishes an actual spillover response from an ordinary dictionary returned by a tool. That distinction lets the application bypass widget data transformation and produce the correct MCP resource link.
- **Line 48:** `preview: dict` — Declare `preview` as `dict` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 49:** `resource_uri: str` — Declare `resource_uri` as `str` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 50:** `original_size_bytes: int` — Declare `original_size_bytes` as `int` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 51:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverResult.to_mcp`

Translate the internal wrapper into an MCP CallToolResult. The same small envelope is available as text and structured data; a separate resource_link points to the complete data without embedding it.

- **Line 52:** `def to_mcp(self) -> types.CallToolResult:` — Define this callable. Translate the internal wrapper into an MCP CallToolResult. The same small envelope is available as text and structured data; a separate resource_link points to the complete data without embedding it.
- **Line 53:** `data = dict(preview=self.preview, resource_uri=self.resource_uri,` — Set `data`: call `dict` with the arguments shown to perform the current operation.
- **Line 54:** `original_size_bytes=self.original_size_bytes)` — Continue building `data` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 55:** `return types.CallToolResult(` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 56:** `content=[` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 57:** `types.TextContent(type="text", text=json.dumps(data, ensure_ascii=False, default=str)),` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 58:** `types.ResourceLink(type="resource_link", uri=self.resource_uri,` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 59:** `name="Full tool result"),` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 60:** `],` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 61:** `structuredContent=data, isError=False,` — Return from this operation: construct the official MCP tool-result model; wire aliases such as structuredContent are handled by that model. No following statements in this call execute.
- **Line 62:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 63:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 64:** `(blank)` — Blank separator for readability; it performs no operation.
### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 65:** `SPILLOVER_SCHEMA = {` — Set `SPILLOVER_SCHEMA`: evaluate `{'type': 'object', 'properties': {'preview': {'type': 'object'}, 'resource_uri': {'type': 'string'}, 'original_size_bytes': {'type': 'integer', 'minimum': 0}}, 'required': ['preview', 'resource_uri', 'original_size_bytes']}`.
- **Line 66:** `"type": "object",` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 67:** `"properties": {` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 68:** `"preview": {"type": "object"},` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 69:** `"resource_uri": {"type": "string"},` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 70:** `"original_size_bytes": {"type": "integer", "minimum": 0},` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 71:** `},` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 72:** `"required": ["preview", "resource_uri", "original_size_bytes"],` — Continue building `SPILLOVER_SCHEMA` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 73:** `}` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 74:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 75:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverRegistry`

Keep resource-prefix-to-store routing private to one application. Anonymous session keys use weak references so this registry does not keep disconnected session objects alive.

- **Line 76:** `class SpilloverRegistry:` — Define this class. Keep resource-prefix-to-store routing private to one application. Anonymous session keys use weak references so this registry does not keep disconnected session objects alive.
- **Line 77:** `"""Per-application routes and weakly held anonymous session identities."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 78:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverRegistry.__init__`

Create separate maps for resource routes and weakly referenced anonymous sessions; neither map is global across applications.

- **Line 79:** `def __init__(self):` — Define this callable. Create separate maps for resource routes and weakly referenced anonymous sessions; neither map is global across applications.
- **Line 80:** `self.routes: dict[str, SpilloverStore] = {}` — Set `self.routes` to the application-private URI-prefix-to-store map: evaluate `{}`.
- **Line 81:** `self._sessions: dict[int, tuple[weakref.ref, str]] = {}` — Set `self._sessions` to the map from live session object IDs to weak references and random ownership keys: evaluate `{}`.
- **Line 82:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverRegistry.register`

Register a prefix and its store while rejecting ambiguous overlapping prefixes. Reusing exactly the same prefix and store is allowed because several tools may share one configured interceptor.

- **Line 83:** `def register(self, interceptor):` — Define this callable. Register a prefix and its store while rejecting ambiguous overlapping prefixes. Reusing exactly the same prefix and store is allowed because several tools may share one configured interceptor.
- **Line 84:** `prefix, store = interceptor.resource_uri_prefix, interceptor.store` — Set `(prefix, store)`: evaluate `(interceptor.resource_uri_prefix, interceptor.store)`.
- **Line 85:** `for existing, registered in self.routes.items():` — Iterate `(existing, registered)` through `self.routes.items()`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 86:** `if prefix.startswith(existing) or existing.startswith(prefix):` — Check evaluate `prefix.startswith(existing) or existing.startswith(prefix)`. The indented body handles this case; other results continue past it.
- **Line 87:** `if prefix != existing or store is not registered:` — Check evaluate `prefix != existing or store is not registered`. The indented body handles this case; other results continue past it.
- **Line 88:** `raise ValueError("Conflicting spillover resource prefixes")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 89:** `self.routes[prefix] = store` — Set `self.routes[prefix]`: evaluate `store`.
- **Line 90:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverRegistry.session_key`

Assign a random server-generated key to a live session object, reusing it for later requests on that object. Client-provided session names and identity metadata are never used as ownership proof.

- **Line 91:** `def session_key(self, rc):` — Define this callable. Assign a random server-generated key to a live session object, reusing it for later requests on that object. Client-provided session names and identity metadata are never used as ownership proof.
- **Line 92:** `session = getattr(rc, "session", None)` — Set `session`: call `getattr` with the arguments shown to perform the current operation.
- **Line 93:** `if session is None:` — Check evaluate `session is None`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 94:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 95:** `identity = id(session)` — Set `identity`: call `id` with the arguments shown to perform the current operation.
- **Line 96:** `existing = self._sessions.get(identity)` — Set `existing`: look up a weakly referenced session record using the actual session object's identity.
- **Line 97:** `if existing is not None and existing[0]() is session:` — Check evaluate `existing is not None and existing[0]() is session`. The indented body handles this case; other results continue past it.
- **Line 98:** `return existing[1]` — Return from this operation: evaluate `existing[1]`. No following statements in this call execute.
- **Line 99:** `key = uuid.uuid4().hex` — Set `key`: evaluate `uuid.uuid4().hex`.
- **Line 100:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 101:** `reference = weakref.ref(session, lambda _: self._sessions.pop(identity, None))` — Set `reference`: retain a non-owning reference and install cleanup when the session is garbage-collected.
- **Line 102:** `except TypeError:` — Handle only the exception types shown; the following body translates the failure or applies the documented fallback.
- **Line 103:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 104:** `self._sessions[identity] = (reference, key)` — Set `self._sessions[identity]`: evaluate `(reference, key)`.
- **Line 105:** `return key` — Return from this operation: evaluate `key`. No following statements in this call execute.
- **Line 106:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 107:** `(blank)` — Blank separator for readability; it performs no operation.
### `caller_owner`

Build a SHA-256 fingerprint from trusted identity dimensions. Authenticated ownership includes issuer, subject, client identity, and tenant; otherwise use the private session key. Invalid credentials and absent trusted identity fail closed.

- **Line 108:** `def caller_owner(context: ExecutionContext) -> str:` — Define this callable. Build a SHA-256 fingerprint from trusted identity dimensions. Authenticated ownership includes issuer, subject, client identity, and tenant; otherwise use the private session key. Invalid credentials and absent trusted identity fail closed.
- **Line 109:** `auth = context.auth` — Set `auth`: evaluate `context.auth`.
- **Line 110:** `if auth is not None and (auth.subject or auth.client_id):` — Check evaluate `auth is not None and (auth.subject or auth.client_id)`. The indented body handles this case; other results continue past it.
- **Line 111:** `identity = ["principal", auth.iss, auth.subject, auth.client_id,` — Set `identity`: evaluate `['principal', auth.iss, auth.subject, auth.client_id, tenant_from_claims(auth.claims)]`.
- **Line 112:** `tenant_from_claims(auth.claims)]` — Continue building `identity` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 113:** `elif context.spillover_invalid_auth:` — Test this next case only if previous branches did not match; the condition shown selects the appropriate handling.
- **Line 114:** `raise PermissionError("Verified identity required for spillover")` — Stop this operation and signal that a trusted caller identity could not be established. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 115:** `elif context.spillover_session_key:` — Test this next case only if previous branches did not match; the condition shown selects the appropriate handling.
- **Line 116:** `identity = ["session", context.spillover_session_key]` — Set `identity`: evaluate `['session', context.spillover_session_key]`.
- **Line 117:** `else:` — Enter the alternative path only when the preceding condition did not match.
- **Line 118:** `raise PermissionError("Spillover requires a verified caller or trusted MCP session")` — Stop this operation and signal that a trusted caller identity could not be established. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 119:** `return hashlib.sha256(json.dumps(identity).encode("utf-8")).hexdigest()` — Return from this operation: return the SHA-256 digest as a stable hexadecimal owner identifier. No following statements in this call execute.
- **Line 120:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 121:** `(blank)` — Blank separator for readability; it performs no operation.
### `bind_spillover_context`

Copy the registry-derived session key into the execution context and mark a supplied-but-unverified token. The interceptor runs after guards, so an auth guard can still establish verified identity before ownership is resolved.

- **Line 122:** `def bind_spillover_context(context, rc, registry):` — Define this callable. Copy the registry-derived session key into the execution context and mark a supplied-but-unverified token. The interceptor runs after guards, so an auth guard can still establish verified identity before ownership is resolved.
- **Line 123:** `context.spillover_session_key = registry.session_key(rc)` — Set `context.spillover_session_key`: obtain the server-generated anonymous ownership key for this live session, or None when no supported session is available.
- **Line 124:** `context.spillover_invalid_auth = bool(` — Set `context.spillover_invalid_auth`: call `bool` with the arguments shown to perform the current operation.
- **Line 125:** `authorization_token_from_request(rc) and context.auth is None` — Continue building `context.spillover_invalid_auth` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 126:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 127:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 128:** `(blank)` — Blank separator for readability; it performs no operation.
### `DataSpilloverInterceptor`

The opt-in interceptor waits for a tool result, preserves control/error responses, serializes successful data, and compares its UTF-8 size with the configured threshold. Only an oversized successful result is stored and replaced with a preview wrapper.

- **Line 129:** `class DataSpilloverInterceptor:` — Define this class. The opt-in interceptor waits for a tool result, preserves control/error responses, serializes successful data, and compares its UTF-8 size with the configured threshold. Only an oversized successful result is stored and replaced with a preview wrapper.
### `DataSpilloverInterceptor.__init__`

Validate the threshold and absolute URI prefix, preserve the supplied store, and allocate a default bounded RAM store only when none was supplied.

- **Line 130:** `def __init__(` — Define this callable. Validate the threshold and absolute URI prefix, preserve the supplied store, and allocate a default bounded RAM store only when none was supplied.
- **Line 131:** `self, threshold_bytes: int = 64 * 1024, store: SpilloverStore | None = None,` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 132:** `resource_uri_prefix: str = "resource://data-spillover/",` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 133:** `):` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 134:** `if type(threshold_bytes) is not int or threshold_bytes < 0:` — Check evaluate `type(threshold_bytes) is not int or threshold_bytes < 0`. The indented body handles this case; other results continue past it.
- **Line 135:** `raise ValueError("Spillover threshold must be a nonnegative integer")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 136:** `parts = urlsplit(resource_uri_prefix)` — Set `parts`: parse the configured resource prefix into URI components so relative addresses, query strings, and fragments can be rejected.
- **Line 137:** `if (not parts.scheme or not parts.netloc or parts.query or parts.fragment` — Check evaluate `not parts.scheme or not parts.netloc or parts.query or parts.fragment or (not resource_uri_prefix.endswith('/'))`. The indented body handles this case; other results continue past it.
- **Line 138:** `or not resource_uri_prefix.endswith("/")):` — Check evaluate `not parts.scheme or not parts.netloc or parts.query or parts.fragment or (not resource_uri_prefix.endswith('/'))`. The indented body handles this case; other results continue past it.
- **Line 139:** `raise ValueError("Resource prefix must be an absolute URI ending in '/'")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 140:** `self.threshold_bytes = threshold_bytes` — Set `self.threshold_bytes` to the boundary above which a successful result spills: evaluate `threshold_bytes`.
- **Line 141:** `self.store = store if store is not None else MemorySpilloverStore()` — Set `self.store` to the storage backend selected for this interceptor: select `store` when `store is not None` is true; otherwise select `MemorySpilloverStore()`.
- **Line 142:** `self.resource_uri_prefix = resource_uri_prefix` — Set `self.resource_uri_prefix` to the prefix prepended to generated store IDs: evaluate `resource_uri_prefix`.
- **Line 143:** `(blank)` — Blank separator for readability; it performs no operation.
### `DataSpilloverInterceptor.intercept`

This is the interceptor execution path. It calls the next pipeline stage once, returns small results by identity, stores oversized results under the caller fingerprint, then returns the preview/URI/original-size wrapper.

- **Line 144:** `async def intercept(self, context, next_fn):` — Define an asynchronous operation that callers await. This is the interceptor execution path. It calls the next pipeline stage once, returns small results by identity, stores oversized results under the caller fingerprint, then returns the preview/URI/original-size wrapper.
- **Line 145:** `result = await next_fn()` — Set `result`: await execute the next interceptor or the actual tool handler exactly once and obtain its result.
- **Line 146:** `if isinstance(result, (InputRequiredResult, SpilloverResult)):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 147:** `return result` — Return from this operation: evaluate `result`. No following statements in this call execute.
- **Line 148:** `if isinstance(result, dict) and result.get("resultType") == "input_required":` — Check evaluate `isinstance(result, dict) and result.get('resultType') == 'input_required'`. The indented body handles this case; other results continue past it.
- **Line 149:** `return result` — Return from this operation: evaluate `result`. No following statements in this call execute.
- **Line 150:** `value = result` — Set `value`: evaluate `result`.
- **Line 151:** `if isinstance(value, types.CallToolResult):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 152:** `if value.is_error or value.result_type == "input_required":` — Check evaluate `value.is_error or value.result_type == 'input_required'`. The indented body handles this case; other results continue past it.
- **Line 153:** `return result` — Return from this operation: evaluate `result`. No following statements in this call execute.
- **Line 154:** `value = value.model_dump(mode="json", by_alias=True, exclude_none=True)` — Set `value`: convert the Pydantic model into JSON-compatible data, using the requested wire aliases.
- **Line 155:** `elif isinstance(value, dict) and value.get("isError"):` — Test this next case only if previous branches did not match; the condition shown selects the appropriate handling.
- **Line 156:** `return result` — Return from this operation: evaluate `result`. No following statements in this call execute.
- **Line 157:** `elif isinstance(value, BaseModel):` — Test this next case only if previous branches did not match; the condition shown selects the appropriate handling.
- **Line 158:** `value = value.model_dump(mode="json")` — Set `value`: convert the Pydantic model into JSON-compatible data, using the requested wire aliases.
- **Line 159:** `if isinstance(value, str):` — Check check the runtime shape before selecting the appropriate serialization or control path. The indented body handles this case; other results continue past it.
- **Line 160:** `payload, mime = value.encode("utf-8"), "text/plain"` — Set `(payload, mime)`: evaluate `(value.encode('utf-8'), 'text/plain')`.
- **Line 161:** `else:` — Enter the alternative path only when the preceding condition did not match.
- **Line 162:** `payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")` — Set `payload` to the complete serialized result as immutable bytes: convert text into the selected byte encoding, so storage and limits operate on bytes.
- **Line 163:** `mime = "application/json"` — Set `mime`: use the literal `'application/json'`.
- **Line 164:** `if len(payload) <= self.threshold_bytes:` — Check evaluate `len(payload) <= self.threshold_bytes`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 165:** `return result` — Return from this operation: evaluate `result`. No following statements in this call execute.
- **Line 166:** `owner = caller_owner(context)` — Set `owner` to the caller fingerprint used for access checks: resolve the trusted caller fingerprint, rejecting missing or failed identity.
- **Line 167:** `key = await self.store.put(payload, owner=owner, mime_type=mime)` — Set `key`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 168:** `return SpilloverResult(generate_preview(value), self.resource_uri_prefix + key, len(payload))` — Return from this operation: call `SpilloverResult` with the arguments shown to perform the current operation. No following statements in this call execute.

## `nitrostack/core/interceptors/spillover.py`

Explains 218 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 1:** `"""Bounded, caller-scoped storage for serialized tool results."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 2:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 3:** `from __future__ import annotations` — Import the named APIs from `__future__` so this file can use the shared SDK, library, or test implementation.
- **Line 4:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 5:** `import asyncio` — Import asyncio for the operations described below; importing binds the module names without calling their functions.
- **Line 6:** `import base64` — Import base64 for the operations described below; importing binds the module names without calling their functions.
- **Line 7:** `import json` — Import json for the operations described below; importing binds the module names without calling their functions.
- **Line 8:** `import math` — Import math for the operations described below; importing binds the module names without calling their functions.
- **Line 9:** `import os` — Import os for the operations described below; importing binds the module names without calling their functions.
- **Line 10:** `import re` — Import re for the operations described below; importing binds the module names without calling their functions.
- **Line 11:** `import threading` — Import threading for the operations described below; importing binds the module names without calling their functions.
- **Line 12:** `import time` — Import time for the operations described below; importing binds the module names without calling their functions.
- **Line 13:** `import uuid` — Import uuid for the operations described below; importing binds the module names without calling their functions.
- **Line 14:** `from collections import OrderedDict` — Import the named APIs from `collections` so this file can use the shared SDK, library, or test implementation.
- **Line 15:** `from dataclasses import dataclass` — Import the named APIs from `dataclasses` so this file can use the shared SDK, library, or test implementation.
- **Line 16:** `from pathlib import Path` — Import the named APIs from `pathlib` so this file can use the shared SDK, library, or test implementation.
- **Line 17:** `from typing import Protocol` — Import the named APIs from `typing` so this file can use the shared SDK, library, or test implementation.
- **Line 18:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 19:** `from nitrostack.core.errors import PayloadTooLargeError` — Import the named APIs from `nitrostack.core.errors` so this file can use the shared SDK, library, or test implementation.
- **Line 20:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 21:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverEntry`

An immutable record holding the exact stored bytes, an opaque owner fingerprint, the media type, and an absolute expiry timestamp. Freezing the record prevents callers from accidentally changing ownership or expiry after retrieval.

- **Line 22:** `@dataclass(frozen=True)` — Generate an immutable data container: field initialization and comparisons are provided, while frozen=True prevents field reassignment.
- **Line 23:** `class SpilloverEntry:` — Define this class. An immutable record holding the exact stored bytes, an opaque owner fingerprint, the media type, and an absolute expiry timestamp. Freezing the record prevents callers from accidentally changing ownership or expiry after retrieval.
- **Line 24:** `payload: bytes` — Declare `payload` as `bytes` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 25:** `owner: str` — Declare `owner` as `str` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 26:** `mime_type: str` — Declare `mime_type` as `str` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 27:** `expires_at: float` — Declare `expires_at` as `float` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 28:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 29:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverStore`

This Python Protocol is the contract a custom storage backend must implement. Its async methods let the interceptor use RAM, disk, or a future backend through the same API; the protocol itself stores no data.

- **Line 30:** `class SpilloverStore(Protocol):` — Define this class. This Python Protocol is the contract a custom storage backend must implement. Its async methods let the interceptor use RAM, disk, or a future backend through the same API; the protocol itself stores no data.
- **Line 31:** `max_bytes: int` — Declare `max_bytes` as `int` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 32:** `ttl_seconds: float` — Declare `ttl_seconds` as `float` in the class contract. This is a type annotation, not an assignment of None; dataclass initialization or the concrete store supplies the runtime value.
- **Line 33:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverStore.put`

Specify the async save contract without implementing it: bytes plus keyword-only owner/MIME type and optional TTL produce an opaque string ID. The ellipsis is a protocol placeholder, not a storage operation.

- **Line 34:** `async def put(` — Define an asynchronous operation that callers await. Specify the async save contract without implementing it: bytes plus keyword-only owner/MIME type and optional TTL produce an opaque string ID. The ellipsis is a protocol placeholder, not a storage operation.
- **Line 35:** `self, payload: bytes, *, owner: str, mime_type: str,` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 36:** `ttl_seconds: float | None = None,` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 37:** `) -> str: ...` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 38:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverStore.get`

Specify the async retrieval contract: a key and required owner produce an authorized, unexpired SpilloverEntry, or None. Implementations must enforce ownership themselves.

- **Line 39:** `async def get(self, key: str, *, owner: str) -> SpilloverEntry | None: ...` — Define an asynchronous operation that callers await. Specify the async retrieval contract: a key and required owner produce an authorized, unexpired SpilloverEntry, or None. Implementations must enforce ownership themselves.
- **Line 40:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverStore.delete`

Specify the async deletion contract: delete an owner-matching entry and report success as a boolean. An incorrect owner cannot remove another caller's data.

- **Line 41:** `async def delete(self, key: str, *, owner: str) -> bool: ...` — Define an asynchronous operation that callers await. Specify the async deletion contract: delete an owner-matching entry and report success as a boolean. An incorrect owner cannot remove another caller's data.
- **Line 42:** `(blank)` — Blank separator for readability; it performs no operation.
### `SpilloverStore.cleanup_expired`

Specify the async cleanup contract: remove expired entries and return how many were removed. A backend chooses the persistence-specific cleanup mechanism.

- **Line 43:** `async def cleanup_expired(self) -> int: ...` — Define an asynchronous operation that callers await. Specify the async cleanup contract: remove expired entries and return how many were removed. A backend chooses the persistence-specific cleanup mechanism.
- **Line 44:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 45:** `(blank)` — Blank separator for readability; it performs no operation.
### `_valid_key`

IDs must contain exactly 32 lowercase hexadecimal characters. This intentionally excludes slashes, backslashes, drive letters, percent escapes, query strings, and parent-directory markers before any filesystem path is built.

- **Line 46:** `def _valid_key(key: str) -> bool:` — Define this callable. IDs must contain exactly 32 lowercase hexadecimal characters. This intentionally excludes slashes, backslashes, drive letters, percent escapes, query strings, and parent-directory markers before any filesystem path is built.
- **Line 47:** `return isinstance(key, str) and re.fullmatch(r"[0-9a-f]{32}", key) is not None` — Return from this operation: evaluate `isinstance(key, str) and re.fullmatch('[0-9a-f]{32}', key) is not None`. No following statements in this call execute.
- **Line 48:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 49:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore`

The RAM implementation uses an OrderedDict as an LRU queue and a byte counter for capacity. A reentrant thread lock protects updates to the queue and counter as one operation; reads update recency but never extend TTL.

- **Line 50:** `class MemorySpilloverStore:` — Define this class. The RAM implementation uses an OrderedDict as an LRU queue and a byte counter for capacity. A reentrant thread lock protects updates to the queue and counter as one operation; reads update recency but never extend TTL.
- **Line 51:** `"""Thread-safe payload cache with absolute TTL and LRU eviction."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 52:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore.__init__`

Validate positive integer storage caps and finite positive TTL, then initialize the empty LRU queue, a reentrant lock, and the byte counter.

- **Line 53:** `def __init__(` — Define this callable. Validate positive integer storage caps and finite positive TTL, then initialize the empty LRU queue, a reentrant lock, and the byte counter.
- **Line 54:** `self, *, max_bytes: int = 10 * 1024 * 1024,` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 55:** `max_total_bytes: int = 100 * 1024 * 1024, ttl_seconds: float = 300,` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 56:** `):` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 57:** `if any(type(limit) is not int or limit <= 0 for limit in (max_bytes, max_total_bytes)):` — Check call `any` with the arguments shown to perform the current operation. The indented body handles this case; other results continue past it.
- **Line 58:** `raise ValueError("Storage limits must be positive integers")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 59:** `if not math.isfinite(ttl_seconds) or ttl_seconds <= 0:` — Check evaluate `not math.isfinite(ttl_seconds) or ttl_seconds <= 0`. The indented body handles this case; other results continue past it.
- **Line 60:** `raise ValueError("TTL must be positive and finite")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 61:** `self.max_bytes = max_bytes` — Set `self.max_bytes` to the maximum allowed size of one decoded payload: evaluate `max_bytes`.
- **Line 62:** `self.max_total_bytes = max_total_bytes` — Set `self.max_total_bytes` to the total storage budget: evaluate `max_total_bytes`.
- **Line 63:** `self.ttl_seconds = ttl_seconds` — Set `self.ttl_seconds` to the default lifetime in seconds: evaluate `ttl_seconds`.
- **Line 64:** `self._entries: OrderedDict[str, SpilloverEntry] = OrderedDict()` — Set `self._entries` to the ordered cache whose first key is the least recently used: call `OrderedDict` with the arguments shown to perform the current operation.
- **Line 65:** `self._lock = threading.RLock()` — Set `self._lock` to the lock protecting each store operation from concurrent mutation: call `threading.RLock` with the arguments shown to perform the current operation.
- **Line 66:** `self._size = 0` — Set `self._size` to the sum of live in-memory payload byte lengths: use the literal `0`.
- **Line 67:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore._entry`

Validate the bytes, ownership, individual size, and TTL before constructing a record. The TTL is converted to an absolute Unix timestamp so the same expiry survives reopening a file store.

- **Line 68:** `def _entry(self, payload, owner, mime_type, ttl_seconds):` — Define this callable. Validate the bytes, ownership, individual size, and TTL before constructing a record. The TTL is converted to an absolute Unix timestamp so the same expiry survives reopening a file store.
- **Line 69:** `if not isinstance(payload, bytes):` — Check evaluate `not isinstance(payload, bytes)`. The indented body handles this case; other results continue past it.
- **Line 70:** `raise TypeError("Store payload must be bytes")` — Stop this operation and reject values that violate the bytes-only storage contract. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 71:** `if not owner:` — Check evaluate `not owner`. The indented body handles this case; other results continue past it.
- **Line 72:** `raise ValueError("A caller owner is required")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 73:** `if len(payload) > self.max_bytes:` — Check evaluate `len(payload) > self.max_bytes`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 74:** `raise PayloadTooLargeError(f"Payload exceeds max_bytes={self.max_bytes}")` — Stop this operation and report that this individual serialized payload exceeds the configured store maximum. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 75:** `ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds` — Set `ttl`: select `self.ttl_seconds` when `ttl_seconds is None` is true; otherwise select `ttl_seconds`.
- **Line 76:** `if not math.isfinite(ttl) or ttl <= 0:` — Check evaluate `not math.isfinite(ttl) or ttl <= 0`. The indented body handles this case; other results continue past it.
- **Line 77:** `raise ValueError("TTL must be positive and finite")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 78:** `return SpilloverEntry(payload, owner, mime_type, time.time() + ttl)` — Return from this operation: call `SpilloverEntry` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 79:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore._remove`

Remove an in-memory entry and subtract exactly its payload length from the capacity counter. Keeping both changes together prevents stale accounting after eviction or deletion.

- **Line 80:** `def _remove(self, key):` — Define this callable. Remove an in-memory entry and subtract exactly its payload length from the capacity counter. Keeping both changes together prevents stale accounting after eviction or deletion.
- **Line 81:** `entry = self._entries.pop(key)` — Set `entry`: remove the named mapping entry and return it when needed.
- **Line 82:** `self._size -= len(entry.payload)` — Update `self._size` using `Sub` and `len(entry.payload)`; this adjusts the running counter or controlled test clock.
- **Line 83:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore._cleanup`

Find entries whose absolute deadlines have passed and remove them using the same accounting helper. The returned number supports cleanup assertions and callers that want to observe expiry work.

- **Line 84:** `def _cleanup(self):` — Define this callable. Find entries whose absolute deadlines have passed and remove them using the same accounting helper. The returned number supports cleanup assertions and callers that want to observe expiry work.
- **Line 85:** `expired = [key for key, entry in self._entries.items() if entry.expires_at <= time.time()]` — Set `expired`: evaluate `[key for key, entry in self._entries.items() if entry.expires_at <= time.time()]`.
- **Line 86:** `for key in expired:` — Iterate `key` through `expired`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 87:** `self._remove(key)` — Delete the cache entry and subtract its payload bytes from the running capacity counter.
- **Line 88:** `return len(expired)` — Return from this operation: measure the value; for a bytes object this is bytes, while string/list/dictionary lengths are characters/items/entries. No following statements in this call execute.
- **Line 89:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore.put`

Create a validated record, remove expired records, evict least-recently-used entries until it fits, assign a new UUID, and update accounting under one lock.

- **Line 90:** `async def put(self, payload, *, owner, mime_type, ttl_seconds=None):` — Define an asynchronous operation that callers await. Create a validated record, remove expired records, evict least-recently-used entries until it fits, assign a new UUID, and update accounting under one lock.
- **Line 91:** `entry = self._entry(payload, owner, mime_type, ttl_seconds)` — Set `entry`: validate the bytes, owner, payload maximum, and TTL, then create an immutable record with an absolute expiry deadline.
- **Line 92:** `if len(payload) > self.max_total_bytes:` — Check evaluate `len(payload) > self.max_total_bytes`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 93:** `raise ValueError("Payload exceeds total store capacity")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 94:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 95:** `self._cleanup()` — Remove expired ram entries and repair the payload-size counter before subsequent cache work.
- **Line 96:** `while self._size + len(payload) > self.max_total_bytes:` — Repeat the indented operation while `self._size + len(payload) > self.max_total_bytes` remains true; each iteration reduces the preview or frees capacity so the loop can finish.
- **Line 97:** `self._remove(next(iter(self._entries)))` — Delete the cache entry and subtract its payload bytes from the running capacity counter.
- **Line 98:** `key = uuid.uuid4().hex` — Set `key`: evaluate `uuid.uuid4().hex`.
- **Line 99:** `self._entries[key] = entry` — Set `self._entries[key]`: evaluate `entry`.
- **Line 100:** `self._size += len(payload)` — Update `self._size` using `Add` and `len(payload)`; this adjusts the running counter or controlled test clock.
- **Line 101:** `return key` — Return from this operation: evaluate `key`. No following statements in this call execute.
- **Line 102:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore.get`

Expire old entries, return None for invalid IDs or wrong owners, and move an authorized hit to the most-recently-used end.

- **Line 103:** `async def get(self, key, *, owner):` — Define an asynchronous operation that callers await. Expire old entries, return None for invalid IDs or wrong owners, and move an authorized hit to the most-recently-used end.
- **Line 104:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 105:** `self._cleanup()` — Remove expired ram entries and repair the payload-size counter before subsequent cache work.
- **Line 106:** `entry = self._entries.get(key) if _valid_key(key) else None` — Set `entry`: select `self._entries.get(key)` when `_valid_key(key)` is true; otherwise select `None`.
- **Line 107:** `if entry is None or entry.owner != owner:` — Check evaluate `entry is None or entry.owner != owner`. The indented body handles this case; other results continue past it.
- **Line 108:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 109:** `self._entries.move_to_end(key)` — Mark this cache key as most recently used for the next lru eviction.
- **Line 110:** `return entry` — Return from this operation: evaluate `entry`. No following statements in this call execute.
- **Line 111:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore.delete`

Only an owner-matching entry may be removed. Missing or wrong-owner entries return False; successful removal updates the byte counter.

- **Line 112:** `async def delete(self, key, *, owner):` — Define an asynchronous operation that callers await. Only an owner-matching entry may be removed. Missing or wrong-owner entries return False; successful removal updates the byte counter.
- **Line 113:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 114:** `entry = self._entries.get(key) if _valid_key(key) else None` — Set `entry`: select `self._entries.get(key)` when `_valid_key(key)` is true; otherwise select `None`.
- **Line 115:** `if entry is None or entry.owner != owner:` — Check evaluate `entry is None or entry.owner != owner`. The indented body handles this case; other results continue past it.
- **Line 116:** `return False` — Return from this operation: use the literal `False`. No following statements in this call execute.
- **Line 117:** `self._remove(key)` — Delete the cache entry and subtract its payload bytes from the running capacity counter.
- **Line 118:** `return True` — Return from this operation: use the literal `True`. No following statements in this call execute.
- **Line 119:** `(blank)` — Blank separator for readability; it performs no operation.
### `MemorySpilloverStore.cleanup_expired`

Expose expiry cleanup as an async contract method while keeping the short RAM mutation inside the thread lock.

- **Line 120:** `async def cleanup_expired(self):` — Define an asynchronous operation that callers await. Expose expiry cleanup as an async contract method while keeping the short RAM mutation inside the thread lock.
- **Line 121:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 122:** `return self._cleanup()` — Return from this operation: remove expired RAM entries and repair the payload-size counter before subsequent cache work. No following statements in this call execute.
- **Line 123:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 124:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore`

The disk implementation reuses configuration and payload validation from the memory implementation, but overrides persistence operations. Each JSON file contains a base64 payload and ownership/expiry metadata; the disk capacity counts the entire file, not just decoded bytes. Worker threads keep disk I/O off the event loop. One instance/process must own each root.

- **Line 125:** `class FileSpilloverStore(MemorySpilloverStore):` — Define this class. The disk implementation reuses configuration and payload validation from the memory implementation, but overrides persistence operations. Each JSON file contains a base64 payload and ownership/expiry metadata; the disk capacity counts the entire file, not just decoded bytes. Worker threads keep disk I/O off the event loop. One instance/process must own each root.
- **Line 126:** `"""Atomic disk store. Each root must be owned by one store instance/process.` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 127:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 128:** `Total capacity counts actual JSON file bytes, including base64 and metadata.` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 129:** `File operations run in worker threads; no background cleanup task is needed.` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 130:** `"""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 131:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore.__init__`

Initialize shared limits, resolve and create a private storage directory, then remove expired records left from previous runs.

- **Line 132:** `def __init__(self, root: str | Path, **kwargs):` — Define this callable. Initialize shared limits, resolve and create a private storage directory, then remove expired records left from previous runs.
- **Line 133:** `super().__init__(**kwargs)` — Initialize the inherited validation and limits, preserving the same storage contract for the disk implementation or configured interceptor subclass.
- **Line 134:** `self.root = Path(root).resolve()` — Set `self.root` to the resolved filesystem storage boundary: resolve the filesystem path or dependency token against its owning API.
- **Line 135:** `self.root.mkdir(mode=0o700, parents=True, exist_ok=True)` — Create the storage directory and necessary parents.
- **Line 136:** `self._cleanup_files()` — Sweep expired uuid-named disk records under the storage root so their space can be reclaimed.
- **Line 137:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore._path`

Convert a validated opaque ID into one JSON filename under the configured root. Reject symlinks and resolved paths outside that root before reading, deleting, or accounting for a file.

- **Line 138:** `def _path(self, key):` — Define this callable. Convert a validated opaque ID into one JSON filename under the configured root. Reject symlinks and resolved paths outside that root before reading, deleting, or accounting for a file.
- **Line 139:** `if not _valid_key(key):` — Check evaluate `not _valid_key(key)`. The indented body handles this case; other results continue past it.
- **Line 140:** `raise ValueError("Invalid spillover ID")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 141:** `path = self.root / f"{key}.json"` — Set `path`: evaluate `self.root / f'{key}.json'`.
- **Line 142:** `if path.is_symlink() or path.resolve().parent != self.root:` — Check evaluate `path.is_symlink() or path.resolve().parent != self.root`. The indented body handles this case; other results continue past it.
- **Line 143:** `raise ValueError("Spillover path escapes storage root")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 144:** `return path` — Return from this operation: evaluate `path`. No following statements in this call execute.
- **Line 145:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore._read`

Reconstruct an immutable entry from a stored JSON document. Base64 validation rejects malformed encoded data rather than silently ignoring invalid characters.

- **Line 146:** `def _read(self, path):` — Define this callable. Reconstruct an immutable entry from a stored JSON document. Base64 validation rejects malformed encoded data rather than silently ignoring invalid characters.
- **Line 147:** `raw = json.loads(path.read_text(encoding="utf-8"))` — Set `raw`: parse stored JSON text back into Python data.
- **Line 148:** `return SpilloverEntry(` — Return from this operation: call `SpilloverEntry` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 149:** `base64.b64decode(raw["payload"], validate=True), raw["owner"],` — Return from this operation: call `SpilloverEntry` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 150:** `raw["mime_type"], raw["expires_at"],` — Return from this operation: call `SpilloverEntry` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 151:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 152:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore._cleanup_files`

Visit only UUID-shaped JSON filenames owned by this store, inspect expiry, and unlink expired files. Other filenames are not treated as spillover entries; invalid owned files fail rather than bypassing safety checks.

- **Line 153:** `def _cleanup_files(self):` — Define this callable. Visit only UUID-shaped JSON filenames owned by this store, inspect expiry, and unlink expired files. Other filenames are not treated as spillover entries; invalid owned files fail rather than bypassing safety checks.
- **Line 154:** `count = 0` — Set `count`: use the literal `0`.
- **Line 155:** `for candidate in self.root.glob("*.json"):` — Iterate `candidate` through `self.root.glob('*.json')`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 156:** `if not _valid_key(candidate.stem):` — Check evaluate `not _valid_key(candidate.stem)`. The indented body handles this case; other results continue past it.
- **Line 157:** `continue` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 158:** `path = self._path(candidate.stem)` — Set `path`: validate the opaque ID and resolve its filename against the store root while rejecting symlinks and escaped paths.
- **Line 159:** `if self._read(path).expires_at <= time.time():` — Check evaluate `self._read(path).expires_at <= time.time()`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 160:** `path.unlink()` — Remove this store-owned file, without deleting the target of any rejected symlink.
- **Line 161:** `count += 1` — Update `count` using `Add` and `1`; this adjusts the running counter or controlled test clock.
- **Line 162:** `return count` — Return from this operation: evaluate `count`. No following statements in this call execute.
- **Line 163:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore._put_file`

Validate and serialize a disk entry, clean expired entries, and check actual disk usage while holding the store lock. Write an exclusively created temporary file and atomically replace it with the final filename so readers do not see partial JSON.

- **Line 164:** `def _put_file(self, payload, owner, mime_type, ttl_seconds):` — Define this callable. Validate and serialize a disk entry, clean expired entries, and check actual disk usage while holding the store lock. Write an exclusively created temporary file and atomically replace it with the final filename so readers do not see partial JSON.
- **Line 165:** `entry = self._entry(payload, owner, mime_type, ttl_seconds)` — Set `entry`: validate the bytes, owner, payload maximum, and TTL, then create an immutable record with an absolute expiry deadline.
- **Line 166:** `encoded = json.dumps({` — Set `encoded`: convert text into the selected byte encoding, so storage and limits operate on bytes.
- **Line 167:** `"payload": base64.b64encode(payload).decode("ascii"),` — Continue building `encoded` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 168:** `"owner": entry.owner, "mime_type": entry.mime_type,` — Continue building `encoded` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 169:** `"expires_at": entry.expires_at,` — Continue building `encoded` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 170:** `}).encode("utf-8")` — Continue building `encoded` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 171:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 172:** `self._cleanup_files()` — Sweep expired uuid-named disk records under the storage root so their space can be reclaimed.
- **Line 173:** `used = sum(self._path(p.stem).stat().st_size for p in self.root.glob("*.json")` — Set `used`: call `sum` with the arguments shown to perform the current operation.
- **Line 174:** `if _valid_key(p.stem))` — Continue building `used` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 175:** `if used + len(encoded) > self.max_total_bytes:` — Check evaluate `used + len(encoded) > self.max_total_bytes`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 176:** `raise ValueError("File store capacity exceeded")` — Stop this operation and reject an invalid configuration, unsafe ID/path, or capacity-exceeding operation. The surrounding SDK or test catches this error; from None suppresses sensitive internal exception details when present.
- **Line 177:** `key = uuid.uuid4().hex` — Set `key`: evaluate `uuid.uuid4().hex`.
- **Line 178:** `target = self._path(key)` — Set `target`: validate the opaque ID and resolve its filename against the store root while rejecting symlinks and escaped paths.
- **Line 179:** `temporary = self.root / f"{key}.tmp"` — Set `temporary`: evaluate `self.root / f'{key}.tmp'`.
- **Line 180:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 181:** `descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)` — Set `descriptor`: exclusively create the temporary file with owner-only POSIX permissions, refusing to follow an existing temporary filename.
- **Line 182:** `with os.fdopen(descriptor, "wb") as stream:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 183:** `stream.write(encoded)` — Write the already serialized document to the exclusive temporary stream.
- **Line 184:** `temporary.replace(target)` — Atomically publish the completed temporary file at its final path.
- **Line 185:** `finally:` — Always run cleanup, whether the preceding work succeeded, failed, or was cancelled.
- **Line 186:** `temporary.unlink(missing_ok=True)` — Remove this store-owned file, without deleting the target of any rejected symlink.
- **Line 187:** `return key` — Return from this operation: evaluate `key`. No following statements in this call execute.
- **Line 188:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore._get_file`

Validate the requested path, clean expired entries, and read the requested record under the same lock. Missing, expired, and wrong-owner records return None; an authorized delete removes the file before returning its record.

- **Line 189:** `def _get_file(self, key, owner, delete=False):` — Define this callable. Validate the requested path, clean expired entries, and read the requested record under the same lock. Missing, expired, and wrong-owner records return None; an authorized delete removes the file before returning its record.
- **Line 190:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 191:** `path = self._path(key)` — Set `path`: validate the opaque ID and resolve its filename against the store root while rejecting symlinks and escaped paths.
- **Line 192:** `self._cleanup_files()` — Sweep expired uuid-named disk records under the storage root so their space can be reclaimed.
- **Line 193:** `if not path.exists():` — Check evaluate `not path.exists()`. The indented body handles this case; other results continue past it.
- **Line 194:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 195:** `entry = self._read(path)` — Set `entry`: decode the validated file's JSON document and reversible base64 payload into a SpilloverEntry.
- **Line 196:** `if entry.expires_at <= time.time():` — Check evaluate `entry.expires_at <= time.time()`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 197:** `path.unlink()` — Remove this store-owned file, without deleting the target of any rejected symlink.
- **Line 198:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 199:** `if entry.owner != owner:` — Check evaluate `entry.owner != owner`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 200:** `return None` — Return from this operation: use the literal `None`. No following statements in this call execute.
- **Line 201:** `if delete:` — Check evaluate `delete`. The indented body handles this case; other results continue past it.
- **Line 202:** `path.unlink()` — Remove this store-owned file, without deleting the target of any rejected symlink.
- **Line 203:** `return entry` — Return from this operation: evaluate `entry`. No following statements in this call execute.
- **Line 204:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore.put`

Pass the blocking atomic-write operation to asyncio.to_thread and await its generated result ID.

- **Line 205:** `async def put(self, payload, *, owner, mime_type, ttl_seconds=None):` — Define an asynchronous operation that callers await. Pass the blocking atomic-write operation to asyncio.to_thread and await its generated result ID.
- **Line 206:** `return await asyncio.to_thread(self._put_file, payload, owner, mime_type, ttl_seconds)` — Return from this operation: await run blocking filesystem work on a worker thread and await its result without blocking the event loop. No following statements in this call execute.
- **Line 207:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore.get`

Run the blocking authorized read in a worker thread and return its entry or None.

- **Line 208:** `async def get(self, key, *, owner):` — Define an asynchronous operation that callers await. Run the blocking authorized read in a worker thread and return its entry or None.
- **Line 209:** `return await asyncio.to_thread(self._get_file, key, owner)` — Return from this operation: await run blocking filesystem work on a worker thread and await its result without blocking the event loop. No following statements in this call execute.
- **Line 210:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore.delete`

Run the authorized read-and-delete operation in a worker thread and translate a returned entry into a success boolean.

- **Line 211:** `async def delete(self, key, *, owner):` — Define an asynchronous operation that callers await. Run the authorized read-and-delete operation in a worker thread and translate a returned entry into a success boolean.
- **Line 212:** `return await asyncio.to_thread(self._get_file, key, owner, True) is not None` — Return from this operation: evaluate `await asyncio.to_thread(self._get_file, key, owner, True) is not None`; the branch runs only when this comparison is true. No following statements in this call execute.
- **Line 213:** `(blank)` — Blank separator for readability; it performs no operation.
### `FileSpilloverStore.cleanup_expired`

Run the locked directory sweep in a worker thread so expiry cleanup does not block asynchronous request handling.

- **Line 214:** `async def cleanup_expired(self):` — Define an asynchronous operation that callers await. Run the locked directory sweep in a worker thread so expiry cleanup does not block asynchronous request handling.
### `FileSpilloverStore.cleanup_expired.cleanup`

These lines connect this operation to the storage and resource flow described above.

- **Line 215:** `def cleanup():` — Define this callable. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 216:** `with self._lock:` — Enter the shown context manager. Locks are released and file streams are closed even if the protected operation raises an exception.
- **Line 217:** `return self._cleanup_files()` — Return from this operation: sweep expired UUID-named disk records under the storage root so their space can be reclaimed. No following statements in this call execute.
### `FileSpilloverStore.cleanup_expired`

Run the locked directory sweep in a worker thread so expiry cleanup does not block asynchronous request handling.

- **Line 218:** `return await asyncio.to_thread(cleanup)` — Return from this operation: await run blocking filesystem work on a worker thread and await its result without blocking the event loop. No following statements in this call execute.

## `nitrostack/core/pipeline.py`

Explains 6 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `use_interceptors`

These lines connect this operation to the storage and resource flow described above.

- **Line 43:** `def use_interceptors(*interceptors: Type[Interceptor] | Interceptor):` — Define this callable. Its body below carries out the surrounding operation; this line defines it without running it.
### `run_pipeline`

These lines connect this operation to the storage and resource flow described above.

- **Line 207:** `interceptors: List[Type[Interceptor] | Interceptor],` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
### `run_pipeline.execute_handler_flow`

These lines connect this operation to the storage and resource flow described above.

- **Line 251:** `interceptor = (` — Set `interceptor`: select `container.resolve(interceptor_cls)` when `isinstance(interceptor_cls, type)` is true; otherwise select `interceptor_cls`.
- **Line 252:** `container.resolve(interceptor_cls)` — Continue building `interceptor` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 253:** `if isinstance(interceptor_cls, type) else interceptor_cls` — Continue building `interceptor` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 254:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.

## `tests/test_spillover.py`

Explains 388 added or replacement lines. Open the source beside this document to follow the exact line numbers.

### `Module setup and shared constants`

These lines connect this operation to the storage and resource flow described above.

- **Line 1:** `"""Issue #67: bounded results, caller isolation, and safe spillover stores."""` — Document the module, class, or operation in a docstring; this explains its contract without changing control flow.
- **Line 2:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 3:** `import asyncio` — Import asyncio for the operations described below; importing binds the module names without calling their functions.
- **Line 4:** `import gc` — Import gc for the operations described below; importing binds the module names without calling their functions.
- **Line 5:** `import json` — Import json for the operations described below; importing binds the module names without calling their functions.
- **Line 6:** `from types import SimpleNamespace` — Import the named APIs from `types` so this file can use the shared SDK, library, or test implementation.
- **Line 7:** `from unittest.mock import MagicMock` — Import the named APIs from `unittest.mock` so this file can use the shared SDK, library, or test implementation.
- **Line 8:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 9:** `import mcp.types as types` — Import mcp.types for the operations described below; importing binds the module names without calling their functions.
- **Line 10:** `import pytest` — Import pytest for the operations described below; importing binds the module names without calling their functions.
- **Line 11:** `from pydantic import BaseModel` — Import the named APIs from `pydantic` so this file can use the shared SDK, library, or test implementation.
- **Line 12:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 13:** `from nitrostack import (` — Import the named APIs from `nitrostack` so this file can use the shared SDK, library, or test implementation.
- **Line 14:** `AuthContext, DataSpilloverInterceptor, DIContainer, ExecutionContext,` — Import or re-export `AuthContext, DataSpilloverInterceptor, DIContainer, ExecutionContext` from `nitrostack` as part of the import block above.
- **Line 15:** `FileSpilloverStore, MemorySpilloverStore, PayloadTooLargeError,` — Import or re-export `FileSpilloverStore, MemorySpilloverStore, PayloadTooLargeError` from `nitrostack` as part of the import block above.
- **Line 16:** `ResourceNotFoundError, generate_preview, module, tool, use_interceptors,` — Import or re-export `ResourceNotFoundError, generate_preview, module, tool, use_interceptors` from `nitrostack` as part of the import block above.
- **Line 17:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 18:** `from nitrostack.core.app import McpApplication` — Import the named APIs from `nitrostack.core.app` so this file can use the shared SDK, library, or test implementation.
- **Line 19:** `from nitrostack.core.interceptors.data_spillover import (` — Import the named APIs from `nitrostack.core.interceptors.data_spillover` so this file can use the shared SDK, library, or test implementation.
- **Line 20:** `SpilloverRegistry, SpilloverResult, caller_owner,` — Import or re-export `SpilloverRegistry, SpilloverResult, caller_owner` from `nitrostack.core.interceptors.data_spillover` as part of the import block above.
- **Line 21:** `)` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 22:** `from nitrostack.protocol.mrtr import InputRequiredResult` — Import the named APIs from `nitrostack.protocol.mrtr` so this file can use the shared SDK, library, or test implementation.
- **Line 23:** `from nitrostack.runtime.request_ctx import RequestContext, request_ctx` — Import the named APIs from `nitrostack.runtime.request_ctx` so this file can use the shared SDK, library, or test implementation.
- **Line 24:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 25:** `(blank)` — Blank separator for readability; it performs no operation.
### `reset_container`

Reset the singleton dependency-injection container before and after each test so provider instances and authentication services cannot leak between cases.

- **Line 26:** `@pytest.fixture(autouse=True)` — Register reusable pytest setup; autouse fixtures apply automatically, and parameters select both storage backends.
- **Line 27:** `def reset_container():` — Define this callable. Reset the singleton dependency-injection container before and after each test so provider instances and authentication services cannot leak between cases.
- **Line 28:** `DIContainer.reset()` — Restore the previous request context or empty dependency container.
- **Line 29:** `yield` — Evaluate `(yield)`.
- **Line 30:** `DIContainer.reset()` — Restore the previous request context or empty dependency container.
- **Line 31:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 32:** `(blank)` — Blank separator for readability; it performs no operation.
### `store`

Run storage contract tests against both RAM and disk with the same limits and TTL. A temporary directory isolates filesystem side effects from the repository and from other test cases.

- **Line 33:** `@pytest.fixture(params=["memory", "file"])` — Register reusable pytest setup; autouse fixtures apply automatically, and parameters select both storage backends.
- **Line 34:** `def store(request, tmp_path):` — Define this callable. Run storage contract tests against both RAM and disk with the same limits and TTL. A temporary directory isolates filesystem side effects from the repository and from other test cases.
- **Line 35:** `cls = MemorySpilloverStore if request.param == "memory" else FileSpilloverStore` — Set `cls`: select `MemorySpilloverStore` when `request.param == 'memory'` is true; otherwise select `FileSpilloverStore`.
- **Line 36:** `args = () if request.param == "memory" else (tmp_path / "results",)` — Set `args`: select `()` when `request.param == 'memory'` is true; otherwise select `(tmp_path / 'results',)`.
- **Line 37:** `return cls(*args, max_bytes=4096, max_total_bytes=16384, ttl_seconds=10)` — Return from this operation: call `cls` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 38:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 39:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_store_roundtrip_owner_delete`

Prove that a saved payload and MIME type round-trip exactly, another owner cannot read or delete it, the original owner can delete it, and the removed entry is missing.

- **Line 40:** `def test_store_roundtrip_owner_delete(store):` — Define this callable. Prove that a saved payload and MIME type round-trip exactly, another owner cannot read or delete it, the original owner can delete it, and the removed entry is missing.
### `test_store_roundtrip_owner_delete.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 41:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 42:** `key = await store.put(b'{"answer":42}', owner="alice", mime_type="application/json")` — Set `key`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 43:** `assert await store.get(key, owner="bob") is None` — Assert `await store.get(key, owner='bob') is None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 44:** `assert not await store.delete(key, owner="bob")` — Assert `not await store.delete(key, owner='bob')`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 45:** `entry = await store.get(key, owner="alice")` — Set `entry`: await look up the requested entry under the supplied ownership constraint.
- **Line 46:** `assert entry.payload == b'{"answer":42}'` — Assert `entry.payload == b'{"answer":42}'`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 47:** `assert entry.mime_type == "application/json"` — Assert `entry.mime_type == 'application/json'`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 48:** `assert await store.delete(key, owner="alice")` — Assert `await store.delete(key, owner='alice')`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 49:** `assert await store.get(key, owner="alice") is None` — Assert `await store.get(key, owner='alice') is None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_store_roundtrip_owner_delete`

Prove that a saved payload and MIME type round-trip exactly, another owner cannot read or delete it, the original owner can delete it, and the removed entry is missing.

- **Line 50:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 51:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 52:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_store_expiry_and_payload_limit`

Control the clock without sleeping. Verify the per-payload error, advance precisely to the expiry boundary, clean it up, and prove the expired resource cannot be retrieved.

- **Line 53:** `def test_store_expiry_and_payload_limit(store, monkeypatch):` — Define this callable. Control the clock without sleeping. Verify the per-payload error, advance precisely to the expiry boundary, clean it up, and prove the expired resource cannot be retrieved.
- **Line 54:** `clock = [1000.0]` — Set `clock`: evaluate `[1000.0]`.
- **Line 55:** `monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])` — Replace this dependency for the duration of the pytest fixture.
### `test_store_expiry_and_payload_limit.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 56:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 57:** `key = await store.put(b"hello", owner="alice", mime_type="text/plain")` — Set `key`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 58:** `with pytest.raises(PayloadTooLargeError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 59:** `await store.put(b"x" * 4097, owner="alice", mime_type="text/plain")` — Await save the full payload with its owner and mime type and obtain an opaque id.
- **Line 60:** `clock[0] += 10` — Update `clock[0]` using `Add` and `10`; this adjusts the running counter or controlled test clock.
- **Line 61:** `assert await store.cleanup_expired() == 1` — Assert `await store.cleanup_expired() == 1`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 62:** `assert await store.get(key, owner="alice") is None` — Assert `await store.get(key, owner='alice') is None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_store_expiry_and_payload_limit`

Control the clock without sleeping. Verify the per-payload error, advance precisely to the expiry boundary, clean it up, and prove the expired resource cannot be retrieved.

- **Line 63:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 64:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 65:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_memory_lru_and_total_capacity`

Fill an eight-byte cache with two four-byte results, access the first, then add a third. The second must be evicted because it is now least recently used, and accounting must stay at eight bytes.

- **Line 66:** `def test_memory_lru_and_total_capacity():` — Define this callable. Fill an eight-byte cache with two four-byte results, access the first, then add a third. The second must be evicted because it is now least recently used, and accounting must stay at eight bytes.
- **Line 67:** `store = MemorySpilloverStore(max_bytes=8, max_total_bytes=8)` — Set `store`: call `MemorySpilloverStore` with the arguments shown to perform the current operation.
### `test_memory_lru_and_total_capacity.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 68:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 69:** `first = await store.put(b"1111", owner="a", mime_type="text/plain")` — Set `first`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 70:** `second = await store.put(b"2222", owner="a", mime_type="text/plain")` — Set `second`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 71:** `await store.get(first, owner="a")` — Await look up the requested entry under the supplied ownership constraint.
- **Line 72:** `await store.put(b"3333", owner="a", mime_type="text/plain")` — Await save the full payload with its owner and mime type and obtain an opaque id.
- **Line 73:** `assert await store.get(second, owner="a") is None` — Assert `await store.get(second, owner='a') is None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 74:** `assert await store.get(first, owner="a") is not None` — Assert `await store.get(first, owner='a') is not None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 75:** `assert store._size == 8` — Assert `store._size == 8`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_memory_lru_and_total_capacity`

Fill an eight-byte cache with two four-byte results, access the first, then add a third. The second must be evicted because it is now least recently used, and accounting must stay at eight bytes.

- **Line 76:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 77:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 78:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_file_capacity_restart_cleanup`

Prove the file capacity includes stored-document overhead, a full store rejects another write, reopening preserves the data, successful writes leave no temporary files, and reopening after TTL removes expired files.

- **Line 79:** `def test_file_capacity_restart_cleanup(tmp_path, monkeypatch):` — Define this callable. Prove the file capacity includes stored-document overhead, a full store rejects another write, reopening preserves the data, successful writes leave no temporary files, and reopening after TTL removes expired files.
- **Line 80:** `clock = [1000.0]` — Set `clock`: evaluate `[1000.0]`.
- **Line 81:** `monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])` — Replace this dependency for the duration of the pytest fixture.
- **Line 82:** `store = FileSpilloverStore(tmp_path, max_total_bytes=300, ttl_seconds=5)` — Set `store`: call `FileSpilloverStore` with the arguments shown to perform the current operation.
### `test_file_capacity_restart_cleanup.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 83:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 84:** `key = await store.put(b"x" * 80, owner="a", mime_type="text/plain")` — Set `key`: await save the full payload with its owner and MIME type and obtain an opaque ID.
- **Line 85:** `with pytest.raises(ValueError, match="capacity"):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 86:** `await store.put(b"x" * 80, owner="a", mime_type="text/plain")` — Await save the full payload with its owner and mime type and obtain an opaque id.
- **Line 87:** `reopened = FileSpilloverStore(tmp_path, max_total_bytes=300)` — Set `reopened`: call `FileSpilloverStore` with the arguments shown to perform the current operation.
- **Line 88:** `assert (await reopened.get(key, owner="a")).payload == b"x" * 80` — Assert `(await reopened.get(key, owner='a')).payload == b'x' * 80`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 89:** `assert list(tmp_path.glob("*.tmp")) == []` — Assert `list(tmp_path.glob('*.tmp')) == []`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 90:** `clock[0] += 5` — Update `clock[0]` using `Add` and `5`; this adjusts the running counter or controlled test clock.
- **Line 91:** `FileSpilloverStore(tmp_path)` — Call `filespilloverstore` with the arguments shown to perform the current operation.
- **Line 92:** `assert list(tmp_path.glob("*.json")) == []` — Assert `list(tmp_path.glob('*.json')) == []`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_file_capacity_restart_cleanup`

Prove the file capacity includes stored-document overhead, a full store rejects another write, reopening preserves the data, successful writes leave no temporary files, and reopening after TTL removes expired files.

- **Line 93:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 94:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 95:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_file_rejects_unsafe_ids`

Exercise traversal, Windows separators, encoded traversal, absolute paths, drive paths, and query characters. Both read and delete must reject them before touching another location.

- **Line 96:** `@pytest.mark.parametrize("key", ["../escape", "..\\escape", "%2e%2e%2fescape", "/etc/passwd", "C:\\escape", "x?y"])` — Ask pytest to run this test once for each supplied case, so distinct shapes or invalid inputs are checked independently.
- **Line 97:** `def test_file_rejects_unsafe_ids(tmp_path, key):` — Define this callable. Exercise traversal, Windows separators, encoded traversal, absolute paths, drive paths, and query characters. Both read and delete must reject them before touching another location.
- **Line 98:** `store = FileSpilloverStore(tmp_path)` — Set `store`: call `FileSpilloverStore` with the arguments shown to perform the current operation.
### `test_file_rejects_unsafe_ids.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 99:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 100:** `with pytest.raises(ValueError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 101:** `await store.get(key, owner="a")` — Await look up the requested entry under the supplied ownership constraint.
- **Line 102:** `with pytest.raises(ValueError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 103:** `await store.delete(key, owner="a")` — Await remove only the requested owner-matching record.
### `test_file_rejects_unsafe_ids`

Exercise traversal, Windows separators, encoded traversal, absolute paths, drive paths, and query characters. Both read and delete must reject them before touching another location.

- **Line 104:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 105:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 106:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_file_rejects_symlink_escape`

Create an actual link from an owned-looking filename to an external file and prove the store rejects it while preserving the external file. Skip only if the OS does not grant link-creation privileges.

- **Line 107:** `def test_file_rejects_symlink_escape(tmp_path):` — Define this callable. Create an actual link from an owned-looking filename to an external file and prove the store rejects it while preserving the external file. Skip only if the OS does not grant link-creation privileges.
- **Line 108:** `root = tmp_path / "results"` — Set `root`: evaluate `tmp_path / 'results'`.
- **Line 109:** `store = FileSpilloverStore(root)` — Set `store`: call `FileSpilloverStore` with the arguments shown to perform the current operation.
- **Line 110:** `outside = tmp_path / "private.json"` — Set `outside`: evaluate `tmp_path / 'private.json'`.
- **Line 111:** `outside.write_text("private")` — Call `outside.write_text` with the arguments shown to perform the current operation.
- **Line 112:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 113:** `(root / ("a" * 32 + ".json")).symlink_to(outside)` — Call `(root / ('a' * 32 + '.json')).symlink_to` with the arguments shown to perform the current operation.
- **Line 114:** `except OSError:` — Handle only the exception types shown; the following body translates the failure or applies the documented fallback.
- **Line 115:** `pytest.skip("Creating symlinks requires OS privileges")` — Call `pytest.skip` with the arguments shown to perform the current operation.
- **Line 116:** `with pytest.raises(ValueError, match="escapes"):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 117:** `asyncio.run(store.get("a" * 32, owner="a"))` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 118:** `assert outside.read_text() == "private"` — Assert `outside.read_text() == 'private'`. A false result fails this test immediately, checking the behavior described for `test_file_rejects_symlink_escape` rather than silently accepting a regression.
- **Line 119:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 120:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_concurrent_store_operations`

Start several writes together and then several reads. IDs must be unique, payload order must round-trip, and lock-protected operations must avoid corruption or accidental replacement.

- **Line 121:** `def test_concurrent_store_operations(store):` — Define this callable. Start several writes together and then several reads. IDs must be unique, payload order must round-trip, and lock-protected operations must avoid corruption or accidental replacement.
### `test_concurrent_store_operations.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 122:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 123:** `keys = await asyncio.gather(*[` — Set `keys`: await schedule the supplied asynchronous operations together and collect their results in input order.
- **Line 124:** `store.put(str(i).encode(), owner="a", mime_type="text/plain") for i in range(20)` — Continue building `keys` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 125:** `])` — Continue building `keys` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 126:** `assert len(set(keys)) == 20` — Assert `len(set(keys)) == 20`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 127:** `entries = await asyncio.gather(*[store.get(key, owner="a") for key in keys])` — Set `entries`: await schedule the supplied asynchronous operations together and collect their results in input order.
- **Line 128:** `assert [entry.payload for entry in entries] == [str(i).encode() for i in range(20)]` — Assert `[entry.payload for entry in entries] == [str(i).encode() for i in range(20)]`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_concurrent_store_operations`

Start several writes together and then several reads. IDs must be unique, payload order must round-trip, and lock-protected operations must avoid corruption or accidental replacement.

- **Line 129:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 130:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 131:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_previews_are_bounded`

Cover ordinary lists/dictionaries, multibyte strings, and deeply nested large values. Counts must describe the original data, truncation must be marked, and serialized preview bytes must stay within 2048.

- **Line 132:** `@pytest.mark.parametrize("value", [list(range(20)), {str(i): i for i in range(20)}, "é" * 10000,` — Ask pytest to run this test once for each supplied case, so distinct shapes or invalid inputs are checked independently.
- **Line 133:** `[{"huge": "x" * 10000}], {"huge": ["x" * 10000]}],` — Continue the preceding Python declaration or expression with the literal name or value shown.
- **Line 134:** `ids=["list", "dict", "unicode", "nested-list", "nested-dict"])` — Continue the preceding Python declaration or expression with the literal name or value shown.
- **Line 135:** `def test_previews_are_bounded(value):` — Define this callable. Cover ordinary lists/dictionaries, multibyte strings, and deeply nested large values. Counts must describe the original data, truncation must be marked, and serialized preview bytes must stay within 2048.
- **Line 136:** `preview = generate_preview(value)` — Set `preview`: build the bounded head/count/text description of the original successful data.
- **Line 137:** `assert preview["count"] == len(value)` — Assert `preview['count'] == len(value)`. A false result fails this test immediately, checking the behavior described for `test_previews_are_bounded` rather than silently accepting a regression.
- **Line 138:** `assert preview["truncated"]` — Assert `preview['truncated']`. A false result fails this test immediately, checking the behavior described for `test_previews_are_bounded` rather than silently accepting a regression.
- **Line 139:** `assert len(json.dumps(preview, ensure_ascii=False).encode()) <= 2048` — Assert `len(json.dumps(preview, ensure_ascii=False).encode()) <= 2048`. A false result fails this test immediately, checking the behavior described for `test_previews_are_bounded` rather than silently accepting a regression.
- **Line 140:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 141:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_threshold_boundary_and_full_payload`

Verify that equal-to-threshold Unicode data returns the exact same object, larger data spills, stored JSON matches the original result, and reported size is bytes rather than characters.

- **Line 142:** `def test_threshold_boundary_and_full_payload(store):` — Define this callable. Verify that equal-to-threshold Unicode data returns the exact same object, larger data spills, stored JSON matches the original result, and reported size is bytes rather than characters.
- **Line 143:** `interceptor = DataSpilloverInterceptor(threshold_bytes=4, store=store)` — Set `interceptor`: call `DataSpilloverInterceptor` with the arguments shown to perform the current operation.
- **Line 144:** `context = ExecutionContext(request_id="1", auth=AuthContext(subject="alice"), logger=MagicMock())` — Set `context`: construct a fresh per-call context; its request ID identifies the operation while identity is populated separately from trusted request sources.
### `test_threshold_boundary_and_full_payload.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 145:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 146:** `for value in ["éé", {"a": 1}, [1, 2]]:` — Iterate `value` through `['éé', {'a': 1}, [1, 2]]`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
### `test_threshold_boundary_and_full_payload.scenario.handler`

These lines connect this operation to the storage and resource flow described above.

- **Line 147:** `async def handler():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 148:** `return value` — Return from this operation: evaluate `value`. No following statements in this call execute.
### `test_threshold_boundary_and_full_payload.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 149:** `result = await interceptor.intercept(context, handler)` — Set `result`: await call `interceptor.intercept` with the arguments shown to perform the current operation.
- **Line 150:** `if value == "éé":` — Check evaluate `value == 'éé'`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 151:** `assert result is value` — Assert `result is value`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 152:** `else:` — Enter the alternative path only when the preceding condition did not match.
- **Line 153:** `assert isinstance(result, SpilloverResult)` — Assert `isinstance(result, SpilloverResult)`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 154:** `entry = await store.get(result.resource_uri.rsplit("/", 1)[1], owner=caller_owner(context))` — Set `entry`: await look up the requested entry under the supplied ownership constraint.
- **Line 155:** `assert json.loads(entry.payload) == value` — Assert `json.loads(entry.payload) == value`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 156:** `assert result.original_size_bytes == len(entry.payload)` — Assert `result.original_size_bytes == len(entry.payload)`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_threshold_boundary_and_full_payload.scenario.unicode_handler`

These lines connect this operation to the storage and resource flow described above.

- **Line 157:** `async def unicode_handler():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 158:** `return "ééé"` — Return from this operation: use the literal `'ééé'`. No following statements in this call execute.
### `test_threshold_boundary_and_full_payload.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 159:** `assert (await interceptor.intercept(context, unicode_handler)).original_size_bytes == 6` — Assert `(await interceptor.intercept(context, unicode_handler)).original_size_bytes == 6`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_threshold_boundary_and_full_payload`

Verify that equal-to-threshold Unicode data returns the exact same object, larger data spills, stored JSON matches the original result, and reported size is bytes rather than characters.

- **Line 160:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 161:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 162:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_control_results_and_missing_identity`

Ensure errors and input-required control responses pass through by identity even with a tiny threshold. A large ordinary result without any trusted caller must raise PermissionError.

- **Line 163:** `def test_control_results_and_missing_identity():` — Define this callable. Ensure errors and input-required control responses pass through by identity even with a tiny threshold. A large ordinary result without any trusted caller must raise PermissionError.
- **Line 164:** `interceptor = DataSpilloverInterceptor(threshold_bytes=1)` — Set `interceptor`: call `DataSpilloverInterceptor` with the arguments shown to perform the current operation.
- **Line 165:** `context = ExecutionContext(request_id="1", logger=MagicMock())` — Set `context`: construct a fresh per-call context; its request ID identifies the operation while identity is populated separately from trusted request sources.
### `test_control_results_and_missing_identity.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 166:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 167:** `controls = [types.CallToolResult(content=[], isError=True),` — Set `controls`: evaluate `[types.CallToolResult(content=[], isError=True), {'resultType': 'input_required', 'message': 'more'}, InputRequiredResult(input_requests=[], request_state={}, message='more')]`.
- **Line 168:** `{"resultType": "input_required", "message": "more"},` — Continue building `controls` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 169:** `InputRequiredResult(input_requests=[], request_state={}, message="more")]` — Continue building `controls` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 170:** `for value in controls:` — Iterate `value` through `controls`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
### `test_control_results_and_missing_identity.scenario.handler`

These lines connect this operation to the storage and resource flow described above.

- **Line 171:** `async def handler():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 172:** `return value` — Return from this operation: evaluate `value`. No following statements in this call execute.
### `test_control_results_and_missing_identity.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 173:** `assert await interceptor.intercept(context, handler) is value` — Assert `await interceptor.intercept(context, handler) is value`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_control_results_and_missing_identity.scenario.large`

These lines connect this operation to the storage and resource flow described above.

- **Line 174:** `async def large():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 175:** `return "large"` — Return from this operation: use the literal `'large'`. No following statements in this call execute.
### `test_control_results_and_missing_identity.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 176:** `with pytest.raises(PermissionError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 177:** `await interceptor.intercept(context, large)` — Await call `interceptor.intercept` with the arguments shown to perform the current operation.
### `test_control_results_and_missing_identity`

Ensure errors and input-required control responses pass through by identity even with a tiny threshold. A large ordinary result without any trusted caller must raise PermissionError.

- **Line 178:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 179:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 180:** `(blank)` — Blank separator for readability; it performs no operation.
### `Session`

A minimal weak-referenceable session stand-in. Object identity, not a caller-supplied string, is the basis of anonymous ownership in these tests.

- **Line 181:** `class Session:` — Define this class. A minimal weak-referenceable session stand-in. Object identity, not a caller-supplied string, is the basis of anonymous ownership in these tests.
- **Line 182:** `pass` — Perform no operation in this intentionally empty class or handled-error branch.
- **Line 183:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 184:** `(blank)` — Blank separator for readability; it performs no operation.
### `ToolInput`

A Pydantic test input model with a default result string. It lets the tests exercise the actual SDK argument validation and tool pipeline.

- **Line 185:** `class ToolInput(BaseModel):` — Define this class. A Pydantic test input model with a default result string. It lets the tests exercise the actual SDK argument validation and tool pipeline.
- **Line 186:** `text: str = "large result"` — Set `text`: use the literal `'large result'`.
- **Line 187:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 188:** `(blank)` — Blank separator for readability; it performs no operation.
### `make_app`

Build a small real SDK application around one decorated tool. Parameters let tests choose a configured interceptor instance or class, task support, and an output schema without duplicating application setup.

- **Line 189:** `def make_app(interceptor, *, task_support="forbidden", output_schema=None):` — Define this callable. Build a small real SDK application around one decorated tool. Parameters let tests choose a configured interceptor instance or class, task support, and an output schema without duplicating application setup.
### `make_app.Tools`

These lines connect this operation to the storage and resource flow described above.

- **Line 190:** `class Tools:` — Define this class. Group the methods below into the provider or test configuration shown.
- **Line 191:** `@tool(name="large", description="Test spillover", input_schema=ToolInput,` — Publish this method as an MCP tool with its input validation and optional task/output metadata.
- **Line 192:** `task_support=task_support, output_schema=output_schema)` — Continue the current declaration or expression using the exact syntax shown; its purpose is described in the enclosing operation above.
- **Line 193:** `@use_interceptors(interceptor)` — Attach this interceptor configuration to the tool method; execution is deferred until the tool pipeline runs.
### `make_app.Tools.large`

These lines connect this operation to the storage and resource flow described above.

- **Line 194:** `async def large(self, input, context):` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 195:** `return {"text": input.text}` — Return from this operation: evaluate `{'text': input.text}`. No following statements in this call execute.
### `make_app`

Build a small real SDK application around one decorated tool. Parameters let tests choose a configured interceptor instance or class, task support, and an output schema without duplicating application setup.

- **Line 196:** `@module(name="spillover-test", providers=[Tools])` — Register the test provider as part of an SDK module so application bootstrap discovers its decorated tool.
### `make_app.Root`

These lines connect this operation to the storage and resource flow described above.

- **Line 197:** `class Root:` — Define this class. Group the methods below into the provider or test configuration shown.
- **Line 198:** `pass` — Perform no operation in this intentionally empty class or handled-error branch.
### `make_app`

Build a small real SDK application around one decorated tool. Parameters let tests choose a configured interceptor instance or class, task support, and an output schema without duplicating application setup.

- **Line 199:** `return McpApplication(Root)` — Return from this operation: call `McpApplication` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 200:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 201:** `(blank)` — Blank separator for readability; it performs no operation.
### `call`

Invoke the SDK tool-call handler while installing an explicit request context. Always reset the ContextVar afterward; task tests use the same application path with explicit task metadata.

- **Line 202:** `async def call(app, rc, text="large result", task=None):` — Define an asynchronous operation that callers await. Invoke the SDK tool-call handler while installing an explicit request context. Always reset the ContextVar afterward; task tests use the same application path with explicit task metadata.
- **Line 203:** `token = request_ctx.set(rc)` — Set `token`: bind the chosen request context locally to the current async execution and obtain a restoration token.
- **Line 204:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 205:** `if task is not None:` — Check evaluate `task is not None`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 206:** `return await app._call_tool("large", {"text": text}, task=task)` — Return from this operation: await call `app._call_tool` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 207:** `handler = app.mcp_server.request_handlers[types.CallToolRequest]` — Set `handler`: evaluate `app.mcp_server.request_handlers[types.CallToolRequest]`.
- **Line 208:** `return (await handler(types.CallToolRequest(` — Return from this operation: evaluate `(await handler(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(name='large', arguments={'text': text})))).root`. No following statements in this call execute.
- **Line 209:** `method="tools/call", params=types.CallToolRequestParams(name="large", arguments={"text": text}),` — Return from this operation: evaluate `(await handler(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(name='large', arguments={'text': text})))).root`. No following statements in this call execute.
- **Line 210:** `))).root` — Return from this operation: evaluate `(await handler(types.CallToolRequest(method='tools/call', params=types.CallToolRequestParams(name='large', arguments={'text': text})))).root`. No following statements in this call execute.
- **Line 211:** `finally:` — Always run cleanup, whether the preceding work succeeded, failed, or was cancelled.
- **Line 212:** `request_ctx.reset(token)` — Restore the request context that existed before the matching set call, preventing caller identity leakage between requests.
- **Line 213:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 214:** `(blank)` — Blank separator for readability; it performs no operation.
### `read`

Invoke the registered resource-read handler under a chosen caller context and return the resource text. The finally block restores context even when access is denied.

- **Line 215:** `async def read(app, rc, uri):` — Define an asynchronous operation that callers await. Invoke the registered resource-read handler under a chosen caller context and return the resource text. The finally block restores context even when access is denied.
- **Line 216:** `token = request_ctx.set(rc)` — Set `token`: bind the chosen request context locally to the current async execution and obtain a restoration token.
- **Line 217:** `try:` — Begin work whose failure must be handled or whose cleanup must always execute.
- **Line 218:** `handler = app.mcp_server.request_handlers[types.ReadResourceRequest]` — Set `handler`: evaluate `app.mcp_server.request_handlers[types.ReadResourceRequest]`.
- **Line 219:** `return (await handler(types.ReadResourceRequest(` — Return from this operation: evaluate `(await handler(types.ReadResourceRequest(method='resources/read', params=types.ReadResourceRequestParams(uri=uri)))).root.contents[0].text`. No following statements in this call execute.
- **Line 220:** `method="resources/read", params=types.ReadResourceRequestParams(uri=uri),` — Return from this operation: evaluate `(await handler(types.ReadResourceRequest(method='resources/read', params=types.ReadResourceRequestParams(uri=uri)))).root.contents[0].text`. No following statements in this call execute.
- **Line 221:** `))).root.contents[0].text` — Return from this operation: evaluate `(await handler(types.ReadResourceRequest(method='resources/read', params=types.ReadResourceRequestParams(uri=uri)))).root.contents[0].text`. No following statements in this call execute.
- **Line 222:** `finally:` — Always run cleanup, whether the preceding work succeeded, failed, or was cancelled.
- **Line 223:** `request_ctx.reset(token)` — Restore the request context that existed before the matching set call, preventing caller identity leakage between requests.
- **Line 224:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 225:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_mcp_resource_roundtrip_and_session_isolation`

Test the full registered tool-call/resource-read path with both storage backends. Verify resource links and original size, allow the originating session, and deny a second session, missing caller, and traversal URI.

- **Line 226:** `def test_mcp_resource_roundtrip_and_session_isolation(store):` — Define this callable. Test the full registered tool-call/resource-read path with both storage backends. Verify resource links and original size, allow the originating session, and deny a second session, missing caller, and traversal URI.
- **Line 227:** `app = make_app(DataSpilloverInterceptor(threshold_bytes=1, store=store))` — Set `app`: construct the real one-tool SDK application used for this test scenario.
- **Line 228:** `alice = RequestContext(session=Session())` — Set `alice`: call `RequestContext` with the arguments shown to perform the current operation.
- **Line 229:** `bob = RequestContext(session=Session())` — Set `bob`: call `RequestContext` with the arguments shown to perform the current operation.
### `test_mcp_resource_roundtrip_and_session_isolation.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 230:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 231:** `result = await call(app, alice)` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 232:** `assert not result.is_error` — Assert `not result.is_error`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 233:** `data = result.structured_content` — Set `data`: evaluate `result.structured_content`.
- **Line 234:** `assert result.content[1].type == "resource_link"` — Assert `result.content[1].type == 'resource_link'`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 235:** `assert json.loads(await read(app, alice, data["resource_uri"])) == {"text": "large result"}` — Assert `json.loads(await read(app, alice, data['resource_uri'])) == {'text': 'large result'}`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 236:** `for caller in [bob, None]:` — Iterate `caller` through `[bob, None]`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 237:** `with pytest.raises(ResourceNotFoundError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 238:** `await read(app, caller, data["resource_uri"])` — Await invoke the application's registered mcp resource-read path under the chosen caller context.
- **Line 239:** `with pytest.raises(ResourceNotFoundError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 240:** `await read(app, alice, "resource://data-spillover/../escape")` — Await invoke the application's registered mcp resource-read path under the chosen caller context.
- **Line 241:** `assert data["original_size_bytes"] == len(b'{"text":"large result"}')` — Assert `data['original_size_bytes'] == len(b'{"text":"large result"}')`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_mcp_resource_roundtrip_and_session_isolation`

Test the full registered tool-call/resource-read path with both storage backends. Verify resource links and original size, allow the originating session, and deny a second session, missing caller, and traversal URI.

- **Line 242:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 243:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 244:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_principal_tenant_issuer_isolation`

Stub only token verification so the SDK still exercises its normal identity extraction. Same-principal access works across session objects; different users, tenants, issuers, and invalid tokens cannot read the result. Unsigned identity metadata does not grant access.

- **Line 245:** `def test_principal_tenant_issuer_isolation(monkeypatch):` — Define this callable. Stub only token verification so the SDK still exercises its normal identity extraction. Same-principal access works across session objects; different users, tenants, issuers, and invalid tokens cannot read the result. Unsigned identity metadata does not grant access.
- **Line 246:** `payloads = {` — Set `payloads`: evaluate `{'alice': {'sub': 'alice', 'iss': 'issuer', 'tenant_id': 'one'}, 'bob': {'sub': 'bob', 'iss': 'issuer', 'tenant_id': 'one'}, 'tenant': {'sub': 'alice', 'iss': 'issuer', 'tenant_id': 'two'}, 'issuer': {'sub': 'alice', 'iss': 'other', 'tenant_id': 'one'}}`.
- **Line 247:** `"alice": {"sub": "alice", "iss": "issuer", "tenant_id": "one"},` — Continue building `payloads` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 248:** `"bob": {"sub": "bob", "iss": "issuer", "tenant_id": "one"},` — Continue building `payloads` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 249:** `"tenant": {"sub": "alice", "iss": "issuer", "tenant_id": "two"},` — Continue building `payloads` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 250:** `"issuer": {"sub": "alice", "iss": "other", "tenant_id": "one"},` — Continue building `payloads` with the field, argument, or value shown. It belongs to the assignment begun above rather than a separate assignment.
- **Line 251:** `}` — Close the continued expression or signature begun above; this delimiter groups its preceding arguments or fields and adds no separate runtime action.
- **Line 252:** `monkeypatch.setattr("nitrostack.auth.request.verify_bearer_payload", payloads.get)` — Replace this dependency for the duration of the pytest fixture.
- **Line 253:** `app = make_app(DataSpilloverInterceptor(threshold_bytes=1))` — Set `app`: construct the real one-tool SDK application used for this test scenario.
### `test_principal_tenant_issuer_isolation.rc`

These lines connect this operation to the storage and resource flow described above.

- **Line 254:** `def rc(token):` — Define this callable. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 255:** `return RequestContext(request=SimpleNamespace(headers={"authorization": "Bearer " + token}),` — Return from this operation: call `RequestContext` with the arguments shown to perform the current operation. No following statements in this call execute.
- **Line 256:** `session=Session(), meta={"userId": "alice", "tenantId": "one"})` — Return from this operation: call `RequestContext` with the arguments shown to perform the current operation. No following statements in this call execute.
### `test_principal_tenant_issuer_isolation.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 257:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 258:** `result = await call(app, rc("alice"))` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 259:** `uri = result.structured_content["resource_uri"]` — Set `uri`: evaluate `result.structured_content['resource_uri']`.
- **Line 260:** `assert json.loads(await read(app, rc("alice"), uri)) == {"text": "large result"}` — Assert `json.loads(await read(app, rc('alice'), uri)) == {'text': 'large result'}`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 261:** `for other in ["bob", "tenant", "issuer", "invalid"]:` — Iterate `other` through `['bob', 'tenant', 'issuer', 'invalid']`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 262:** `with pytest.raises(ResourceNotFoundError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 263:** `await read(app, rc(other), uri)` — Await invoke the application's registered mcp resource-read path under the chosen caller context.
- **Line 264:** `assert (await call(app, rc("invalid"))).is_error` — Assert `(await call(app, rc('invalid'))).is_error`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_principal_tenant_issuer_isolation`

Stub only token verification so the SDK still exercises its normal identity extraction. Same-principal access works across session objects; different users, tenants, issuers, and invalid tokens cannot read the result. Unsigned identity metadata does not grant access.

- **Line 265:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 266:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 267:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_class_decorator_custom_prefix_and_schema`

Check backward-compatible class-based interceptor construction, a custom resource prefix, and output-schema advertisement that accepts the original object or a spillover envelope.

- **Line 268:** `def test_class_decorator_custom_prefix_and_schema():` — Define this callable. Check backward-compatible class-based interceptor construction, a custom resource prefix, and output-schema advertisement that accepts the original object or a spillover envelope.
### `test_class_decorator_custom_prefix_and_schema.Configured`

These lines connect this operation to the storage and resource flow described above.

- **Line 269:** `class Configured(DataSpilloverInterceptor):` — Define this class. Group the methods below into the provider or test configuration shown.
### `test_class_decorator_custom_prefix_and_schema.Configured.__init__`

These lines connect this operation to the storage and resource flow described above.

- **Line 270:** `def __init__(self):` — Define this callable. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 271:** `super().__init__(threshold_bytes=1, resource_uri_prefix="resource://custom/")` — Initialize the inherited validation and limits, preserving the same storage contract for the disk implementation or configured interceptor subclass.
### `test_class_decorator_custom_prefix_and_schema`

Check backward-compatible class-based interceptor construction, a custom resource prefix, and output-schema advertisement that accepts the original object or a spillover envelope.

- **Line 272:** `schema = {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}` — Set `schema`: evaluate `{'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text']}`.
- **Line 273:** `app = make_app(Configured, output_schema=schema)` — Set `app`: construct the real one-tool SDK application used for this test scenario.
### `test_class_decorator_custom_prefix_and_schema.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 274:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 275:** `rc = RequestContext(session=Session())` — Set `rc`: call `RequestContext` with the arguments shown to perform the current operation.
- **Line 276:** `result = await call(app, rc)` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 277:** `assert result.structured_content["resource_uri"].startswith("resource://custom/")` — Assert `result.structured_content['resource_uri'].startswith('resource://custom/')`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 278:** `definition = app._build_tool_definition(app._tools["large"])` — Set `definition`: call `app._build_tool_definition` with the arguments shown to perform the current operation.
- **Line 279:** `original = definition.output_schema["anyOf"][0]` — Set `original`: evaluate `definition.output_schema['anyOf'][0]`.
- **Line 280:** `assert all(original[key] == value for key, value in schema.items())` — Assert `all((original[key] == value for key, value in schema.items()))`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 281:** `assert definition.output_schema["anyOf"][1]["required"] == ["preview", "resource_uri", "original_size_bytes"]` — Assert `definition.output_schema['anyOf'][1]['required'] == ['preview', 'resource_uri', 'original_size_bytes']`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_class_decorator_custom_prefix_and_schema`

Check backward-compatible class-based interceptor construction, a custom resource prefix, and output-schema advertisement that accepts the original object or a spillover envelope.

- **Line 282:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 283:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 284:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_registry_conflicts_and_session_cleanup`

Allow shared use of one store/prefix, reject a different store claiming it, reuse a live session key, and prove garbage collection removes the registry entry.

- **Line 285:** `def test_registry_conflicts_and_session_cleanup():` — Define this callable. Allow shared use of one store/prefix, reject a different store claiming it, reuse a live session key, and prove garbage collection removes the registry entry.
- **Line 286:** `registry = SpilloverRegistry()` — Set `registry`: call `SpilloverRegistry` with the arguments shown to perform the current operation.
- **Line 287:** `interceptor = DataSpilloverInterceptor()` — Set `interceptor`: call `DataSpilloverInterceptor` with the arguments shown to perform the current operation.
- **Line 288:** `registry.register(interceptor)` — Call `registry.register` with the arguments shown to perform the current operation.
- **Line 289:** `registry.register(interceptor)` — Call `registry.register` with the arguments shown to perform the current operation.
- **Line 290:** `with pytest.raises(ValueError, match="Conflicting"):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 291:** `registry.register(DataSpilloverInterceptor())` — Call `registry.register` with the arguments shown to perform the current operation.
- **Line 292:** `rc = RequestContext(session=Session())` — Set `rc`: call `RequestContext` with the arguments shown to perform the current operation.
- **Line 293:** `assert registry.session_key(rc) == registry.session_key(rc)` — Assert `registry.session_key(rc) == registry.session_key(rc)`. A false result fails this test immediately, checking the behavior described for `test_registry_conflicts_and_session_cleanup` rather than silently accepting a regression.
- **Line 294:** `del rc` — Delete the local reference so garbage collection can exercise weak-reference cleanup; this does not itself delete a stored file.
- **Line 295:** `gc.collect()` — Call `gc.collect` with the arguments shown to perform the current operation.
- **Line 296:** `assert registry._sessions == {}` — Assert `registry._sessions == {}`. A false result fails this test immediately, checking the behavior described for `test_registry_conflicts_and_session_cleanup` rather than silently accepting a regression.
- **Line 297:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 298:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_mcp_expiry_and_store_errors`

Check resource expiry at the MCP boundary and verify an oversized result becomes a tool error containing the limit explanation, rather than returning the original large data.

- **Line 299:** `def test_mcp_expiry_and_store_errors(store, monkeypatch):` — Define this callable. Check resource expiry at the MCP boundary and verify an oversized result becomes a tool error containing the limit explanation, rather than returning the original large data.
- **Line 300:** `clock = [1000.0]` — Set `clock`: evaluate `[1000.0]`.
- **Line 301:** `monkeypatch.setattr("nitrostack.core.interceptors.spillover.time.time", lambda: clock[0])` — Replace this dependency for the duration of the pytest fixture.
- **Line 302:** `app = make_app(DataSpilloverInterceptor(threshold_bytes=1, store=store))` — Set `app`: construct the real one-tool SDK application used for this test scenario.
- **Line 303:** `rc = RequestContext(session=Session())` — Set `rc`: call `RequestContext` with the arguments shown to perform the current operation.
### `test_mcp_expiry_and_store_errors.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 304:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 305:** `result = await call(app, rc)` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 306:** `uri = result.structured_content["resource_uri"]` — Set `uri`: evaluate `result.structured_content['resource_uri']`.
- **Line 307:** `clock[0] += 10` — Update `clock[0]` using `Add` and `10`; this adjusts the running counter or controlled test clock.
- **Line 308:** `with pytest.raises(ResourceNotFoundError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 309:** `await read(app, rc, uri)` — Await invoke the application's registered mcp resource-read path under the chosen caller context.
- **Line 310:** `oversized = await call(app, rc, text="x" * 5000)` — Set `oversized`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 311:** `assert oversized.is_error` — Assert `oversized.is_error`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 312:** `assert "max_bytes" in oversized.content[0].text` — Assert `'max_bytes' in oversized.content[0].text`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 313:** `assert "x" * 5000 not in oversized.content[0].text` — Assert `'x' * 5000 not in oversized.content[0].text`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_mcp_expiry_and_store_errors`

Check resource expiry at the MCP boundary and verify an oversized result becomes a tool error containing the limit explanation, rather than returning the original large data.

- **Line 314:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 315:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 316:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_models_mcp_results_and_widget_bypass`

Check JSON-compatible serialization of Pydantic models and explicit MCP results. Verify the internal spillover wrapper converts to a link without passing through a widget transformer that expects the original data.

- **Line 317:** `def test_models_mcp_results_and_widget_bypass(store):` — Define this callable. Check JSON-compatible serialization of Pydantic models and explicit MCP results. Verify the internal spillover wrapper converts to a link without passing through a widget transformer that expects the original data.
- **Line 318:** `interceptor = DataSpilloverInterceptor(threshold_bytes=1, store=store)` — Set `interceptor`: call `DataSpilloverInterceptor` with the arguments shown to perform the current operation.
- **Line 319:** `context = ExecutionContext(request_id="1", auth=AuthContext(subject="alice"), logger=MagicMock())` — Set `context`: construct a fresh per-call context; its request ID identifies the operation while identity is populated separately from trusted request sources.
- **Line 320:** `app = make_app(interceptor)` — Set `app`: construct the real one-tool SDK application used for this test scenario.
### `test_models_mcp_results_and_widget_bypass.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 321:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 322:** `for value in [ToolInput(text="full"), types.CallToolResult(` — Iterate `value` through `[ToolInput(text='full'), types.CallToolResult(content=[types.TextContent(type='text', text='full')], structuredContent={'text': 'full'})]`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 323:** `content=[types.TextContent(type="text", text="full")], structuredContent={"text": "full"},` — Iterate `value` through `[ToolInput(text='full'), types.CallToolResult(content=[types.TextContent(type='text', text='full')], structuredContent={'text': 'full'})]`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 324:** `)]:` — Iterate `value` through `[ToolInput(text='full'), types.CallToolResult(content=[types.TextContent(type='text', text='full')], structuredContent={'text': 'full'})]`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
### `test_models_mcp_results_and_widget_bypass.scenario.handler`

These lines connect this operation to the storage and resource flow described above.

- **Line 325:** `async def handler():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 326:** `return value` — Return from this operation: evaluate `value`. No following statements in this call execute.
### `test_models_mcp_results_and_widget_bypass.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 327:** `spilled = await interceptor.intercept(context, handler)` — Set `spilled`: await call `interceptor.intercept` with the arguments shown to perform the current operation.
- **Line 328:** `payload = await store.get(spilled.resource_uri.rsplit("/", 1)[1], owner=caller_owner(context))` — Set `payload` to the complete serialized result as immutable bytes: await look up the requested entry under the supplied ownership constraint.
- **Line 329:** `assert json.loads(payload.payload) == value.model_dump(mode="json", by_alias=True, exclude_none=True)` — Assert `json.loads(payload.payload) == value.model_dump(mode='json', by_alias=True, exclude_none=True)`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 330:** `component = MagicMock()` — Set `component`: call `MagicMock` with the arguments shown to perform the current operation.
- **Line 331:** `result = app._to_call_tool_result(spilled, component, context)` — Set `result`: call `app._to_call_tool_result` with the arguments shown to perform the current operation.
- **Line 332:** `assert result.structured_content["resource_uri"] == spilled.resource_uri` — Assert `result.structured_content['resource_uri'] == spilled.resource_uri`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 333:** `component.transformer.assert_not_called()` — Prove that this potentially destructive or incompatible operation was never invoked.
### `test_models_mcp_results_and_widget_bypass`

Check JSON-compatible serialization of Pydantic models and explicit MCP results. Verify the internal spillover wrapper converts to a link without passing through a widget transformer that expects the original data.

- **Line 334:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 335:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 336:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_background_task_spills_and_can_be_read`

Create an actual background tool task, wait a bounded time for completion, inspect its saved result, and read its spillover URI using the initiating session.

- **Line 337:** `def test_background_task_spills_and_can_be_read():` — Define this callable. Create an actual background tool task, wait a bounded time for completion, inspect its saved result, and read its spillover URI using the initiating session.
- **Line 338:** `app = make_app(DataSpilloverInterceptor(threshold_bytes=1), task_support="optional")` — Set `app`: construct the real one-tool SDK application used for this test scenario.
- **Line 339:** `rc = RequestContext(session=Session())` — Set `rc`: call `RequestContext` with the arguments shown to perform the current operation.
### `test_background_task_spills_and_can_be_read.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 340:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 341:** `result = await call(app, rc, task=types.TaskMetadata(ttl=60000))` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 342:** `for _ in range(100):` — Iterate `_` through `range(100)`. Each iteration applies the indented behavior to one entry, interceptor, caller, or test case.
- **Line 343:** `entry = await app.task_manager._store.get(result.task.task_id)` — Set `entry`: await look up the requested entry under the supplied ownership constraint.
- **Line 344:** `if entry.result is not None:` — Check evaluate `entry.result is not None`; the branch runs only when this comparison is true. The indented body handles this case; other results continue past it.
- **Line 345:** `break` — Stop polling as soon as the background task has produced its stored result.
- **Line 346:** `await asyncio.sleep(0.01)` — Await yield to the background task before checking its completion again.
- **Line 347:** `assert entry.result is not None` — Assert `entry.result is not None`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 348:** `stored = entry.result` — Set `stored`: evaluate `entry.result`.
- **Line 349:** `data = stored.structured_content if isinstance(stored, types.CallToolResult) else stored["structuredContent"]` — Set `data`: select `stored.structured_content` when `isinstance(stored, types.CallToolResult)` is true; otherwise select `stored['structuredContent']`.
- **Line 350:** `assert json.loads(await read(app, rc, data["resource_uri"])) == {"text": "large result"}` — Assert `json.loads(await read(app, rc, data['resource_uri'])) == {'text': 'large result'}`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
### `test_background_task_spills_and_can_be_read`

Create an actual background tool task, wait a bounded time for completion, inspect its saved result, and read its spillover URI using the initiating session.

- **Line 351:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 352:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 353:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_opaque_oauth_identity_reads_resources`

Exercise opaque-token introspection through the configured OAuth service. Verified identity allows spillover without a session; a subsequently inactive token cannot read the saved result.

- **Line 354:** `def test_opaque_oauth_identity_reads_resources(monkeypatch):` — Define this callable. Exercise opaque-token introspection through the configured OAuth service. Verified identity allows spillover without a session; a subsequently inactive token cannot read the saved result.
- **Line 355:** `from nitrostack.auth.oauth import OAuthService` — Import the named APIs from `nitrostack.auth.oauth` so this file can use the shared SDK, library, or test implementation.
- **Line 356:** `from unittest.mock import AsyncMock` — Import the named APIs from `unittest.mock` so this file can use the shared SDK, library, or test implementation.
- **Line 357:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 358:** `monkeypatch.setattr("nitrostack.auth.request.verify_bearer_payload", lambda token: None)` — Replace this dependency for the duration of the pytest fixture.
- **Line 359:** `service = MagicMock()` — Set `service`: call `MagicMock` with the arguments shown to perform the current operation.
- **Line 360:** `service.introspect_token = AsyncMock(return_value={"active": True, "sub": "alice", "iss": "oauth"})` — Set `service.introspect_token`: call `AsyncMock` with the arguments shown to perform the current operation.
- **Line 361:** `DIContainer.get_instance().register_value(OAuthService, service)` — Register this preconfigured service instance with dependency injection.
- **Line 362:** `app = make_app(DataSpilloverInterceptor(threshold_bytes=1))` — Set `app`: construct the real one-tool SDK application used for this test scenario.
- **Line 363:** `rc = RequestContext(request=SimpleNamespace(headers={"authorization": "Bearer opaque"}))` — Set `rc`: call `RequestContext` with the arguments shown to perform the current operation.
### `test_opaque_oauth_identity_reads_resources.scenario`

These lines connect this operation to the storage and resource flow described above.

- **Line 364:** `async def scenario():` — Define an asynchronous operation that callers await. Its body below carries out the surrounding operation; this line defines it without running it.
- **Line 365:** `result = await call(app, rc)` — Set `result`: await invoke the application's registered tool-call path under the chosen request context.
- **Line 366:** `uri = result.structured_content["resource_uri"]` — Set `uri`: evaluate `result.structured_content['resource_uri']`.
- **Line 367:** `assert json.loads(await read(app, rc, uri)) == {"text": "large result"}` — Assert `json.loads(await read(app, rc, uri)) == {'text': 'large result'}`. A false result fails this test immediately, checking the behavior described for `scenario` rather than silently accepting a regression.
- **Line 368:** `service.introspect_token.return_value = {"active": False}` — Set `service.introspect_token.return_value`: evaluate `{'active': False}`.
- **Line 369:** `with pytest.raises(ResourceNotFoundError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 370:** `await read(app, rc, uri)` — Await invoke the application's registered mcp resource-read path under the chosen caller context.
### `test_opaque_oauth_identity_reads_resources`

Exercise opaque-token introspection through the configured OAuth service. Verified identity allows spillover without a session; a subsequently inactive token cannot read the saved result.

- **Line 371:** `asyncio.run(scenario())` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 372:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 373:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_file_symlink_check_without_os_privileges`

Simulate the path library reporting a symlink so the rejection branch is covered even on Windows machines that cannot create real links. The separate real-link test covers the actual filesystem behavior where supported.

- **Line 374:** `def test_file_symlink_check_without_os_privileges(tmp_path, monkeypatch):` — Define this callable. Simulate the path library reporting a symlink so the rejection branch is covered even on Windows machines that cannot create real links. The separate real-link test covers the actual filesystem behavior where supported.
- **Line 375:** `from pathlib import Path` — Import the named APIs from `pathlib` so this file can use the shared SDK, library, or test implementation.
- **Line 376:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 377:** `store = FileSpilloverStore(tmp_path)` — Set `store`: call `FileSpilloverStore` with the arguments shown to perform the current operation.
- **Line 378:** `monkeypatch.setattr(Path, "is_symlink", lambda path: path.name == "a" * 32 + ".json")` — Replace this dependency for the duration of the pytest fixture.
- **Line 379:** `with pytest.raises(ValueError, match="escapes"):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 380:** `asyncio.run(store.get("a" * 32, owner="a"))` — Create an event loop, execute this test scenario to completion, and close the loop.
- **Line 381:** `(blank)` — Blank separator for readability; it performs no operation.
- **Line 382:** `(blank)` — Blank separator for readability; it performs no operation.
### `test_invalid_interceptor_configuration`

Reject negative or non-integral byte thresholds and prefixes that are relative or contain a query component.

- **Line 383:** `@pytest.mark.parametrize("kwargs", [{"threshold_bytes": -1}, {"threshold_bytes": 0.5},` — Ask pytest to run this test once for each supplied case, so distinct shapes or invalid inputs are checked independently.
- **Line 384:** `{"resource_uri_prefix": "../results/"},` — Continue the preceding Python declaration or expression with the literal name or value shown.
- **Line 385:** `{"resource_uri_prefix": "resource://results/?x/"}])` — Continue the preceding Python declaration or expression with the literal name or value shown.
- **Line 386:** `def test_invalid_interceptor_configuration(kwargs):` — Define this callable. Reject negative or non-integral byte thresholds and prefixes that are relative or contain a query component.
- **Line 387:** `with pytest.raises(ValueError):` — Require the operation inside this block to raise the specified exception; a successful unsafe operation causes the test to fail.
- **Line 388:** `DataSpilloverInterceptor(**kwargs)` — Call `dataspilloverinterceptor` with the arguments shown to perform the current operation.

## README usage examples, line by line

The README contains additional example code rather than runtime SDK implementation. The Python and JSON examples added for this feature are explained here too.

- **README line 699:** `from pydantic import BaseModel` — Import the public SDK or Pydantic APIs used by the example; users do not need private implementation imports.
- **README line 700:** `from nitrostack import DataSpilloverInterceptor, MemorySpilloverStore, tool, use_interceptors` — Import the public SDK or Pydantic APIs used by the example; users do not need private implementation imports.
- **README line 701:** `(blank)` — Separate the example into readable declarations; this line has no runtime effect.
- **README line 702:** `spillover = DataSpilloverInterceptor(` — Create one configured interceptor instance that the tool decorator below will reuse for calls.
- **README line 703:** `threshold_bytes=64 * 1024,` — Set the spill threshold to 64 × 1024 = 65,536 serialized bytes. Equal-sized results remain unchanged; larger results move into the store.
- **README line 704:** `store=MemorySpilloverStore(` — Choose RAM storage, which provides TTL and LRU eviction and does not survive a process restart.
- **README line 705:** `max_bytes=10 * 1024 * 1024,` — Allow one decoded stored result to occupy at most 10 MiB; exceeding this raises PayloadTooLargeError rather than evicting enough entries to accept it.
- **README line 706:** `max_total_bytes=100 * 1024 * 1024,` — Set a 100 MiB store capacity. RAM counts payload bytes; the file store counts actual JSON document bytes including base64 and metadata overhead.
- **README line 707:** `ttl_seconds=300,` — Keep each result available for five minutes from its insertion time. Reads never extend this lifetime.
- **README line 708:** `),` — Close the surrounding constructor call or JSON object. This punctuation completes the example syntax without adding a separate operation.
- **README line 709:** `resource_uri_prefix="resource://data-spillover/",` — Use the default MCP resource-address namespace; the store-generated opaque ID is appended to this prefix.
- **README line 710:** `)` — Close the surrounding constructor call or JSON object. This punctuation completes the example syntax without adding a separate operation.
- **README line 711:** `(blank)` — Separate the example into readable declarations; this line has no runtime effect.
- **README line 712:** `class ReportInput(BaseModel):` — Define the Pydantic model used to validate the incoming report tool arguments.
- **README line 713:** `count: int = 10000` — Accept an integer row count and default it to 10,000 when the client omits the argument.
- **README line 714:** `(blank)` — Separate the example into readable declarations; this line has no runtime effect.
- **README line 715:** `class ReportTools:` — Group the report method in a provider/controller class that must be registered in the user application module.
- **README line 716:** `@tool(name="report", description="Generate a report", input_schema=ReportInput)` — Expose the report method as an MCP tool, publish its description, and use ReportInput for argument validation.
- **README line 717:** `@use_interceptors(spillover)` — Attach the configured spillover instance to this method. The pipeline will run it around each report call.
- **README line 718:** `async def report(self, input: ReportInput, context):` — Declare the asynchronous report handler. The SDK passes validated input and an execution context containing caller information.
- **README line 719:** `return [{"row": index} for index in range(input.count)]` — Build the complete list of row dictionaries. The interceptor measures the returned JSON, saves it if oversized, and replaces it with a small preview and URI.
- **README line 730:** `{` — Close the surrounding constructor call or JSON object. This punctuation completes the example syntax without adding a separate operation.
- **README line 731:** `"jsonrpc": "2.0",` — Identify the request as a JSON-RPC 2.0 message.
- **README line 732:** `"id": 2,` — Assign the client's request identifier so it can associate the resource response with this request.
- **README line 733:** `"method": "resources/read",` — Ask the MCP server to read a resource rather than invoke the original tool again.
- **README line 734:** `"params": {"uri": "resource://data-spillover/<id-from-tool-result>"}` — Supply the exact resource_uri returned by the earlier tool result. The angle-bracket text is a placeholder, and knowing a URI alone does not bypass caller ownership checks.
- **README line 735:** `}` — Close the surrounding constructor call or JSON object. This punctuation completes the example syntax without adding a separate operation.
- **README line 746:** `from nitrostack import FileSpilloverStore` — Import the public SDK or Pydantic APIs used by the example; users do not need private implementation imports.
- **README line 747:** `(blank)` — Separate the example into readable declarations; this line has no runtime effect.
- **README line 748:** `store = FileSpilloverStore(` — Create the disk-backed alternative; pass this instance as the interceptor's store when persistence is desired.
- **README line 749:** `".nitrostack/spillover",` — Choose a storage directory relative to the running server's working directory. The store resolves it to an absolute root and confines generated files there.
- **README line 750:** `max_bytes=10 * 1024 * 1024,` — Allow one decoded stored result to occupy at most 10 MiB; exceeding this raises PayloadTooLargeError rather than evicting enough entries to accept it.
- **README line 751:** `max_total_bytes=100 * 1024 * 1024,` — Set a 100 MiB store capacity. RAM counts payload bytes; the file store counts actual JSON document bytes including base64 and metadata overhead.
- **README line 752:** `ttl_seconds=300,` — Keep each result available for five minutes from its insertion time. Reads never extend this lifetime.
- **README line 753:** `)` — Close the surrounding constructor call or JSON object. This punctuation completes the example syntax without adding a separate operation.

## Changes to existing behavior

- Class-based interceptors still resolve through the singleton DI container. Configured instances are accepted directly; only interceptor resolution changes, not guards, middleware, pipes, or filters.
- Undecorated tools retain their original behavior and output schema. Decorated tools advertise an alternative spillover envelope when they already have an output schema.
- Resource-not-found is the common outward error for expired, unknown, malformed, or unauthorized spillover addresses. A storage write error becomes the existing tool-error response; the large result is not sent as a fallback.
- Widgets receive ordinary data as before until a result spills. A spilled result bypasses the widget transformer because its envelope is not the original tool data.
- The previous feature branch and stash are preserved. The implementation branch is feature/issue-67-data-spillover.

Total Python lines explained: **879**. The README adds usage examples and operational constraints; it is documentation, rather than another executable implementation.

## Verification results

- Spillover acceptance tests: **38 passed, 1 skipped**. The real symlink test is skipped because this Windows account cannot create symlinks. A separate test simulates symlink detection and passes.
- Initial focused regression set (spillover, pipeline ordering, testing harness, tool schemas): **46 passed, 1 skipped**, before the additional task/OAuth/expiry acceptance tests were added.
- Full suite with coverage, after refreshing the editable SDK version metadata: **868 passed, 1 failed, 1 skipped**, with **85.80%** total coverage. This exceeds the repository's configured 50% coverage floor.
- The remaining failure was `tests/test_oauth.py::test_discovery_dcr_404_when_disabled_or_missing_client_id`, where Windows aborted a localhost HTTP connection with WinError 10053.
- The entire OAuth test module passed separately (**34 tests**). An unchanged `main` archive also passed its full suite (**831 tests**, 85.41% coverage). These results do not establish a specific root cause for the full-run connection failure.
- A subsequent complete run without coverage produced **867 passed, 2 failed, 1 skipped**: the same OAuth test and `test_discovery_server_idempotent_start_and_dcr_http` failed with the same connection-aborted error. The feature's acceptance tests continued to pass.
- The full repository suite therefore is **not entirely green on this Windows host**. No production OAuth code or existing OAuth tests were changed to mask that result.
- `git diff --check` passed. At the time of verification, the implementation was uncommitted and unpushed on `feature/issue-67-data-spillover`.

The local virtual environment needed the already-declared `pytest-timeout` development dependency. Its editable SDK installation was refreshed from stale version 0.3.2 to the pulled source version 0.4.2; this resolved an unrelated upgrade-test version mismatch without changing dependency declarations.
