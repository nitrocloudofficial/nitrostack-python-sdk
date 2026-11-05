# OAuth client and token stores

`OAuth2Client` manages OAuth credentials for an application calling a protected
service. It is separate from the SDK's server-side OAuth guards. It saves every
successfully acquired or refreshed token to its configured store before returning
it. It does not launch browsers, receive callbacks, or intercept API requests.

## Client credentials

```python
import asyncio
import os

from nitrostack import OAuth2Client, OAuth2ClientConfig, is_token_expired


async def main():
    client = OAuth2Client(OAuth2ClientConfig(
        client_id="reporting-app",
        client_secret=os.environ["OAUTH_CLIENT_SECRET"],
        token_endpoint="https://identity.example.com/oauth/token",
    ))
    key = "identity.example.com|reporting-app|service-account|reports-api"
    token = await client.store.get_token(key)
    if token is None or is_token_expired(token):
        token = await client.get_client_credentials_token(
            key, scope="reports:read", resource="https://reports.example.com",
        )
    # Pass token.access_token to your HTTP client's Authorization header.
    # Do not print the credential or its serialized representation.


asyncio.run(main())
```

An equivalent mapping with snake_case keys can replace `OAuth2ClientConfig`.
Confidential clients authenticate using HTTP Basic; public clients send their
client ID without a secret. Token requests use form encoding. HTTP uses the
standard library, with a default 10-second socket timeout and a 1-MiB response
limit. Override these using `timeout_seconds` and `max_response_bytes`.

## Authorization code and PKCE

```python
from nitrostack import OAuth2Client

client = OAuth2Client({
    "client_id": "desktop-app",
    "issuer": "https://identity.example.com",
    "redirect_uri": "http://127.0.0.1:8765/callback",
})

# Run these awaits inside your application's async lifecycle.
metadata = await client.discover_authorization_server_metadata()
flow = await client.start_authorization_flow(scope="profile", resource="https://api.example.com")
# Your application presents flow.authorization_url and securely retains flow.
```

The application must associate the pending flow with the correct user/session,
validate the callback's state against `flow.state`, reject unsolicited/replayed
callbacks, and consume the pending flow once. Only after that validation should it
exchange the code:

```python
token = await client.exchange_code_for_token(
    "identity.example.com|desktop-app|user-123|api.example.com",
    code=callback_code,
    code_verifier=flow.code_verifier,
    resource="https://api.example.com",
)
```

The SDK reuses its S256 PKCE implementation and generates random state when none
is supplied. `AuthorizationFlow` hides the URL, state and verifier in its repr.
The application still needs to protect those explicit attributes. No callback
listener is supplied. PKCE capability validation applies to authorization-code
flows after discovery; a client-credentials-only server does not need PKCE.

## Discovery and configured endpoints

`discover_protected_resource_metadata(resource_url,
www_authenticate_header=...)` supports a Bearer challenge's `resource_metadata`
URL and path-specific/root well-known locations. Without a supplied challenge,
it first probes the resource for its challenge. Its returned `authorization_servers`
can be used with `discover_authorization_server_metadata(issuer=...)`.

Authorization-server discovery validates the issuer and endpoint fields before
replacing cached metadata. Explicit constructor endpoints take precedence over
discovered endpoints. Discovery does not register the application automatically;
`register_client(metadata)` is a separate JSON request and returns a redacted
`ClientRegistration` containing the returned credentials.

HTTPS is required. HTTP is allowed only for explicitly configured loopback
development endpoints. Constructor endpoint URLs are trusted operator settings;
untrusted destinations are DNS-checked and connected using the validated IP,
preserving the original hostname for TLS verification/SNI and the original port.
Redirects are rejected. Root resource paths are permitted without weakening CIMD's
existing non-root client-identifier rule. CIMD retains its separate document limit.

## Storage and key selection

The default `MemoryTokenStore` writes no files. Both it and `FileTokenStore`
implement the asynchronous `TokenStore` protocol: `save_token`, `get_token`,
`delete_token`, `list_keys`, and `clear`.

Applications sharing a store must construct keys that distinguish provider,
client, principal, and resource. The store does not infer identity or add access
control between code sharing the same store. Avoid secrets in key names.

```python
from pathlib import Path
from nitrostack import FileTokenStore, OAuth2Client

store = FileTokenStore(Path.home() / ".my-app" / "oauth-tokens.json")
client = OAuth2Client(config, store=store)
```

File persistence is explicit. `create_default_token_store()` selects
`~/.nitrostack/tokens.json`; `get_default_store_path()` returns that path. Neither
changes the OAuth client's default memory storage.

File updates serialize under a canonical-path, process-local lock and atomically
replace the destination using an exclusive temporary file in the same directory.
Failed serialization or writes preserve the previous valid file. Symlink
destinations are refused. A missing file is empty; corruption, decryption and
permission failures raise `TokenStoreError` rather than silently discarding data.
Filesystem operations run in worker threads.

On POSIX, destination and temporary credential files use mode `0600` before
credentials are written. Windows uses ACLs rather than Unix permission semantics;
use an application-private directory with suitable ACLs. Cross-process concurrent
writers are unsupported. Keep token files out of source control and backups that
are not approved for credentials.

### Optional encryption

```shell
pip install 'nitrostack[token-encryption]'
```

```python
store = FileTokenStore(
    Path.home() / ".my-app" / "oauth-tokens.enc",
    encryption_key=os.environ["TOKEN_STORE_ENCRYPTION_KEY"],
)
```

Encryption imports are lazy. The file format remains compatible with PR #4:
AES-GCM with a random 16-byte IV, hex `iv:tag:ciphertext`, and its existing
PBKDF2-HMAC-SHA256 key derivation (100,000 iterations, `nitrostack-token-store`
salt). Keep the encryption key separate from the file. Switching an existing
plaintext file to encryption requires an explicit migration by the application.

## Expiry and refresh

`StoredToken` is immutable. `expires_at` is an integer Unix timestamp in
**milliseconds**. `calculate_expiration(expires_in)` and `is_token_expired(token,
skew=30)` take **seconds**. Missing/`None` `expires_in` uses one hour; zero expires
immediately. Negative, nonnumeric, boolean and nonfinite durations are rejected.

Stores retain expired records so the refresh credential remains available:

```python
token = await client.store.get_token(key)
if token is not None and is_token_expired(token):
    if token.refresh_token:
        token = await client.refresh_token(key)
    else:
        # Start the appropriate acquisition flow again.
        ...
```

Share one client instance to coalesce concurrent refresh calls for a key into one
upstream request. Different keys run independently. Cancelling a waiter does not
cancel the shared refresh. Completed/failed tasks are removed so a later call can
retry. Refresh preserves an omitted refresh token and scope, and the existing
resource association. Persistence failures are reported; failed refreshes leave
the local record unchanged. A provider may already have rotated a credential when
a local persistence failure occurs, so applications must handle reauthorization.

Acquisition, refresh and revocation serialize for the same key within the client.
Revocation selects the refresh credential when available (or the access token),
and deletes the local record only after upstream success. Pass
`token_type_hint="access_token"` to select that credential explicitly. An empty
successful revocation response is accepted. Coordination is not shared between
client instances or processes.

## Errors and secret handling

Catch `OAuthClientError` and `TokenStoreError` for sanitized failures. OAuth errors
expose the operation, HTTP status when available, and recognized standard error
codes. Upstream error descriptions, URLs with query parameters, request bodies and
underlying exception details are suppressed.

Config, token, pending-flow and registration representations hide credentials.
Explicit `token.to_dict()` and `registration.to_dict()` contain credentials for
interoperability/persistence: **never log these mappings**. Redacted repr does not
make arbitrary attribute access, application logs or memory dumps safe.
