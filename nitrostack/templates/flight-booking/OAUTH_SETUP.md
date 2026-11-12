# OAuth 2.1 Server Setup Guide

The TypeScript SDK only enforces OAuth when `OAUTH_REQUIRED=true`. Python now
matches that: **Studio and Inspector work without a token** against mock Duffel
flights (`DUFFEL_API_KEY=your-duffel-api-key`). Set `OAUTH_REQUIRED=true` only
when you want real Bearer-token checks.

To run your flight booking MCP server with OAuth 2.1 protection, you need to configure an OAuth authorization server (like Keycloak, Auth0, Hydra, or a local mock OAuth server).

## 1. Local Configuration

Add the following environment variables to your `.env` file to configure resource protection:

```env
# --- Enforcement gate -------------------------------------------------------
# Unset / false (default): tokens are NOT enforced. Studio and Inspector can
#   call tools without authenticating, against mock data. Best for local dev.
# true: Bearer tokens are enforced. If no verifier (JWKS_URI or an
#   introspection endpoint) is configured, the server still starts but rejects
#   every protected request -- fail closed, never fail open.
OAUTH_REQUIRED=true

# --- Server identity --------------------------------------------------------
RESOURCE_URI=http://localhost:3000/mcp
AUTH_SERVER_URL=https://your-tenant.us.auth0.com

# --- Token verification: pick ONE of the two ---------------------------------
# 1) JWKS -- verifies signatures locally, no network call per request.
JWKS_URI=https://your-tenant.us.auth0.com/.well-known/jwks.json

# 2) Or RFC 7662 introspection -- asks the authorization server per token.
#    Both spellings are accepted; OAUTH_INTROSPECTION_ENDPOINT wins if both set.
# OAUTH_INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
# INTROSPECTION_ENDPOINT=http://localhost:3000/oauth/introspect
# INTROSPECTION_CLIENT_ID=your-introspection-client-id
# INTROSPECTION_CLIENT_SECRET=your-introspection-client-secret

# --- Token claim validation --------------------------------------------------
# Audience must match, or the token is rejected (RFC 8707). Defaults to RESOURCE_URI.
TOKEN_AUDIENCE=http://localhost:3000/mcp
TOKEN_ISSUER=https://your-tenant.us.auth0.com/

# --- Dynamic Client Registration (RFC 7591, optional, off by default) --------
# Serves only the statically configured client below. Requires BOTH the flag
# and OAUTH_CLIENT_ID -- without a client id it stays disabled.
# OAUTH_ENABLE_CLIENT_REGISTRATION=true
# OAUTH_CLIENT_ID=your-client-id
# OAUTH_CLIENT_SECRET=your-client-secret

# --- Tuning ------------------------------------------------------------------
# Seconds to cache a successful introspection result (default 300; 0 disables).
# OAUTH_TOKEN_CACHE_SECONDS=300
# Port for the .well-known discovery server (default 3005).
# OAUTH_DISCOVERY_PORT=3005
# OAUTH_DISCOVERY_AUTO_RETRY=true
# OAUTH_DISCOVERY_MAX_RETRIES=50
```

## 2. Protected Routes

The tools in this server use the `@use_guards(OAuthGuard, create_scope_guard([...]))` decorators to automatically protect endpoints:
* **Public**: No guards (or custom public filters).
* **Read-Protected**: Requires valid access token with `read` scope.
* **Write-Protected**: Requires valid access token with `write` scope.

When calling protected tools, the client must pass a valid Bearer token in the `Authorization` header.
