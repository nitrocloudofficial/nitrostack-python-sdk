# Issue #68 implementation walkthrough

This change adds client-side OAuth support alongside the existing server-side
authentication code. The starting point is the two unmerged files from PR #4;
their responsibilities and encrypted file format are retained, while their
concurrency, persistence, validation and transport behavior are revised.

## Token records and time

`auth/token_store.py` defines `StoredToken` as a frozen dataclass. A frozen record
lets stores return the same object without allowing a caller to mutate a stored
credential accidentally. Construction validates the required token strings,
integer millisecond deadline and optional string fields. Credential-bearing
fields have `repr=False`; explicit `to_dict()` is deliberately different because
persistence needs their real values.

`calculate_expiration()` validates a finite nonnegative numeric duration, converts
seconds to milliseconds, adds the current Unix time, and returns an integer.
`is_token_expired()` compares the clock with that deadline minus a configurable
margin, also converting its seconds to milliseconds. Equality counts as expired.

`token_response_to_stored()` is the conversion boundary for provider responses.
Only an absent or null expiry receives the one-hour fallback. Zero remains zero.
During refresh, a missing refresh token or scope inherits the previous value;
the original resource association remains attached to the replacement record.

## The storage contract

The asynchronous `TokenStore` protocol describes the five operations needed by
the client without requiring inheritance. Applications can provide another store
by implementing that contract. `MemoryTokenStore` holds immutable records under
a small process lock. It deliberately does not purge expired access tokens,
because doing so would also discard refresh credentials.

`FileTokenStore` delegates blocking work to `asyncio.to_thread()`. Its lock is
selected by canonical path, so different instances targeting the same file share
the same process-local lock. The complete load/modify/save operation holds that
lock, preventing a stale reader from overwriting another caller's newly saved key.

The read path rejects symlinks, opens without following the final symlink where
supported, requires a regular file, and tightens POSIX permissions. Missing files
mean an empty store. Invalid JSON, invalid records, decryption failures and
permission failures remain errors rather than becoming an empty document.

The write path performs these steps:

1. Serialize and optionally encrypt all records before touching the destination.
2. Create the parent directory and an exclusive temporary file beside the target.
3. Set restrictive permissions before writing credentials.
4. Write, flush and synchronize the temporary file.
5. Recheck the destination and replace it atomically.
6. Remove a remaining temporary file when a write or replacement fails.

The old destination remains intact until replacement. Tests inject failures at
serialization, synchronization and replacement to verify this property.

Encryption is imported only when selected. AES-GCM, key derivation and the hex
`iv:tag:ciphertext` representation preserve the older implementation's format.
`token-encryption` advertises the optional dependency, and the development extra
includes it so encryption tests always execute. Neither store coordinates
multiple writer processes; Windows directory privacy depends on ACLs.

## Client configuration and transport

`auth/client.py` defines typed, redacted config, authorization-flow and
registration results. Constructor mappings use the same snake_case names as the
config model. The client creates a memory store unless the caller supplies one.
Request timeout and response limits are validated once at construction.

The transport has two layers. `_request()` owns validation, DNS safety, status
handling and sanitized errors. `_send()` is an asynchronous seam that delegates
urllib work to a worker thread; tests can replace this seam without contacting an
identity provider. `_send_sync()` builds the request, rejects redirects, bounds
the response before JSON parsing, and captures status and headers. Revocation
does not require JSON, allowing an empty successful response.

Constructor endpoint URLs represent explicit operator configuration. Other
destinations call the existing CIMD safety checker. The returned address is used
for the actual socket connection; the original hostname is retained for TLS
certificate checks and SNI, and the URL's port is preserved. Proxy routing is
disabled for pinned requests so a proxy cannot defeat the selected destination.

`auth/cimd.py` now separates generic fetch URL validation from the CIMD-specific
non-root document-path rule. Existing CIMD entry points retain their original
default. OAuth can validate a root resource URL explicitly. The existing pinned
connection classes and redirect denial are shared; the CIMD document size limit
remains independent from the OAuth response limit.

## Discovery and authorization

Protected-resource discovery considers an explicit metadata URL first, otherwise
a Bearer challenge and well-known candidates. It requires the returned resource
identifier to match and validates the listed authorization-server URLs.

Authorization-server discovery probes the supported well-known locations and
checks the exact expected issuer plus relevant endpoint fields. It replaces
cached metadata only after the entire document validates. Endpoint lookup always
checks explicit configuration before discovery results.

Authorization flow creation reuses `generate_pkce_params("S256")`, creates random
state unless the caller supplies it, and constructs the authorization query with
the client ID, redirect URI, challenge and optional scope/resource. Existing
reserved query parameters are replaced rather than duplicated. The returned
verifier and state belong to the application's pending flow; callback receipt,
state validation and replay prevention remain application responsibilities.

PKCE metadata checks apply when starting authorization-code flows. Generic
discovery and client-credentials grants do not require PKCE advertising.

## Acquisition, refresh and revocation

Code exchange and client-credentials acquisition share `_obtain_token()`. It
constructs a form request, applies Basic authentication for confidential clients,
converts the response to `StoredToken`, and awaits persistence before returning.
A failed save is a reported failure, not an apparently successful acquisition.

`refresh_token()` maintains a dictionary from token key to the in-progress task.
The first caller creates the task; simultaneous callers await that same task.
`asyncio.shield()` separates a waiter's cancellation from the shared operation.
A completion callback removes the task and retrieves its exception, including
when all waiters cancelled. A failed operation can therefore be retried later.

Different keys have different locks and refresh tasks. Same-key acquisition,
refresh and revocation hold a shared lock within the client instance. In
particular, revocation waits for an already-running refresh and then revokes the
rotated credential, preventing that refresh from restoring a record after local
logout. The lock registry uses weak references so unused keys do not accumulate
permanent lock entries.

Refresh reads the existing record even when expired, uses its refresh credential,
preserves omitted fields, and saves the replacement. Failure leaves the local
record unchanged. A provider-side rotation can still have happened before a local
save failure, which an application must handle by reauthorization if necessary.

Revocation defaults to the stored refresh token when available and otherwise the
access token. An explicit hint selects either credential. It removes the local
record only after upstream success. Client registration sends JSON and returns a
redacted typed result; it does not implicitly change client configuration.

## Error boundaries and verification

`OAuthClientError` exposes operation, HTTP status and whitelisted standard OAuth
codes. Arbitrary provider descriptions and underlying exception messages are
suppressed with sanitized exceptions. Token-store errors follow the same rule.
Tests use recognizable credential sentinels to inspect representations,
tracebacks, logs, stdout and stderr. Explicit serialization remains secret-bearing.

The three new test modules cover storage/expiry, OAuth lifecycle/concurrency, and
urllib/pinned transport behavior respectively. They use fake upstream responses
and controlled coordination events instead of live providers. Existing OAuth,
PKCE, CIMD, auth and full-suite tests exercise integration with the SDK. See the
separate verification report for exact platform results and the Windows socket
limitation; local Linux runs are not a claim of hosted CI success.
