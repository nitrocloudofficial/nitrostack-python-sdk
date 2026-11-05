# Investigation: TypeScript `DiscoveryHttpServer` vs the Python SDK (#77)

Checked against: Python `nitrostack-python-sdk` `develop` at `fb0faf8`;
TypeScript `nitrocloudofficial/nitrostack` at `10859bef`. Date: 2026-11-03.

## 1. What the TypeScript server does
File: `typescript/packages/core/src/core/transports/discovery-http-server.ts`

- Options (`:11-18`): `port`, `autoRetry`, `maxRetries`.
- Defaults (`:53-67`): with an options object, `port` 3005, `autoRetry` true,
  `maxRetries` 10. With the legacy numeric constructor, `autoRetry` false.
- Retry (`:23-45`, `:97-109`): when `autoRetry` is on, `findAvailablePort` probes
  `port`, `port+1`, ... up to `maxRetries` using a throwaway `net` server, then
  listens on the first free port. It throws if none is free.
- Without `autoRetry`, an `EADDRINUSE` error is logged with a hint to set
  `OAUTH_DISCOVERY_PORT` and the start promise rejects (`:117-127`).
- Reporting (`:93-95`): `getPort()` returns the port actually used.
- Routing (`:72-87`): exact-match path handlers registered with `on(path, handler)`;
  unknown paths return 404. Bind address is not passed to `listen`, so Node's default applies.

## 2. Consumers in the TypeScript stack
| Consumer | File:line | Use |
|---|---|---|
| `OAuthModule.start()` | `core/oauth-module.ts:560-569` | In stdio mode, creates the server with `autoRetry` (env `OAUTH_DISCOVERY_AUTO_RETRY`, default on, `:549`) and `maxRetries: 50` (`:554`), reads `getPort()`, then emits the port to the client. |
| `OAuthModule.start()` fallback | `core/oauth-module.ts:610-617` | Starts the same server if the HTTP transport is missing in http/dual mode. |
| `registerDiscoveryHandlers` | `core/oauth-module.ts:667-673` | Registers `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource`, and `/oauth/v2/register` when client registration is enabled. |
| `notifyClientAboutOAuth` | `core/oauth-module.ts:639-657` | Writes `[NITROSTACK_OAUTH]{...}[/NITROSTACK_OAUTH]` to stderr with the actual port and well-known URLs. |
| CLI template docs | `packages/cli/templates/typescript-oauth/OAUTH_SETUP.md:97,210,350` | Documents port 3005 and the startup log line. |
| Tests | `transport.http.test.ts:111-172`, `transports.test.ts:244-288`, `oauth.extended.test.ts:39-41` | Cover the retry and no-retry options (ports 34508/34509). |

Within `typescript/packages`, outside tests and templates, the only consumer is
`OAuthModule`. No other code in that tree (including the CLI sources) references it.
**Not checked:** NitroStudio's source is not in this repository, so whether Studio
parses the `[NITROSTACK_OAUTH]` marker could not be verified.

## 3. Current Python behaviour
| Concern | Location | Behaviour |
|---|---|---|
| Startup | `nitrostack/core/app.py:1958-1966` | `McpApplication.start()` calls `OAuthService.start_discovery_server()` only when an `OAuthService` is registered in the container. |
| Bind | `nitrostack/auth/oauth.py:259-274` | A daemon thread builds `HTTPServer(("localhost", port), DiscoveryHandler)` once. No probing and no retry. |
| Port setting | `nitrostack/auth/oauth.py:261` | `int(os.environ.get("OAUTH_DISCOVERY_PORT", self.discovery_port))`: the **environment variable wins** over the `for_root(discovery_port=...)` value. `oauth_module.py` contains no port logic. |
| Failure | `nitrostack/auth/oauth.py:275-277` | Any exception is written to stderr and swallowed. The thread ends, `_server` stays `None`, and the app keeps running with no discovery server. |
| Reporting | `nitrostack/auth/oauth.py:264-273` | Writes `[NITROSTACK_OAUTH]{port, resource_uri, authorization_servers}[/NITROSTACK_OAUTH]` to stderr. The TypeScript marker carries more fields: `type`, `scopesSupported` and `wellKnownEndpoints`. |
| CLI ports | `nitrostack/cli/main.py` `_resolve_runtime_ports`, `_normalize_port` | Resolve the MCP and widget ports (`--port`, `--widget`, env, defaults 3000/3001). Unrelated to OAuth discovery; no retry. |

### Probe: preferred port already in use
`scripts/probe_discovery_port.py` reserves a port, then starts the Python discovery server on it.
Output on Windows (Python 3.12):
busy port: 58380
Failed to start OAuth Discovery Server on port 58380: [WinError 10013] An attempt was made to access a socket in a way forbidden by its access permissions
server object: None
bound to: None
GET failed: TimeoutError timed out

The server does not start, no port is reported, and the application is not told.
Note: Windows returned `WinError 10013` rather than the usual address-in-use error,
which is how it reports binding to a port held by another socket here.

## 4. Comparison
| Capability | TypeScript | Python | Gap? |
|---|---|---|---|
| Separate discovery server | yes, stdio mode and as fallback | yes, always a background thread when OAuth is registered | no |
| Preferred port | option, default 3005, env `OAUTH_DISCOVERY_PORT` | env or `discovery_port` | no |
| Retry on busy port | `autoRetry`, `maxRetries` (50 in OAuthModule) | none | **yes** |
| Actual port available to code | `getPort()` and `OAuthModule.discoveryInfo` | no | yes |
| Port reported to the host tool | stderr marker with port and well-known URLs | stderr marker with port only | partial |
| Behaviour on bind failure | start promise rejects | logged and swallowed; no server | yes |
| Only started when OAuth is configured | yes | yes | no |

## 5. Conclusion
**Open an implementation issue; do not close this one.** The two servers match in
purpose, endpoints and stderr marker. They differ in port conflicts: TypeScript tries
up to 50 ports and reports the one it used, while Python makes one attempt and silently
runs without discovery when the port is taken, as the probe shows. A client that reads
the marker will then never receive a port.

Proposed scope for the follow-up (link here once opened):
- Retry on a busy port, controlled by `OAUTH_DISCOVERY_AUTO_RETRY` and a max-retries
  limit, with the same defaults as TypeScript (on, 50).
- Store the actual port on `OAuthService` and put it in the stderr marker.
- Surface bind failures instead of swallowing them.
- Tests: busy port with retry, retries exhausted, port reported.

Out of scope: matching the TypeScript marker's extra fields, until it is known
whether NitroStudio consumes them.