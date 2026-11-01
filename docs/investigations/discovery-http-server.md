# Investigation: TypeScript `DiscoveryHttpServer` vs the Python SDK (#77)

Checked against: Python `develop` at `<git rev-parse --short upstream/develop>`,
TypeScript `<repo>@<commit>`. Date: 2026-10-31.

## 1. What the TypeScript server does
- File: `core/transports/discovery-http-server.ts`
- Options: `port`, `autoRetry`, `maxRetries` (defaults: `<fill in>`)
- Binds: `<host/interface>`; endpoints served: `<list>`
- Retry behaviour: `<on EADDRINUSE, tries port+1 up to maxRetries?>`
- How the chosen port is reported: `<log line / return value / file>`

## 2. Consumers in the TypeScript stack
| Consumer | File:line | How it uses the server |
|---|---|---|
| `<CLI / Studio / OAuth module / tests>` | `<path:line>` | `<starts it / reads its port / ...>` |

TODO: if no consumer outside tests exists, say so explicitly.

## 3. Current Python behaviour
| Concern | Python location | Behaviour |
|---|---|---|
| OAuth discovery server | `nitrostack/auth/oauth.py` `OAuthService.start_discovery_server` | Background `http.server`, started from `McpApplication.start()` only when `OAuthService` is registered (`core/app.py`). Port from `<OAUTH_DISCOVERY_PORT / discovery_port>`. |
| Port reporting | `nitrostack/auth/oauth.py` | Prints a `[NITROSTACK_OAUTH]{...}[/NITROSTACK_OAUTH]` marker to stderr: `<what fields, who parses it>` |
| MCP and widget ports | `nitrostack/cli/main.py` `_resolve_runtime_ports`, `_normalize_port` | Explicit `--port`/`--widget` flags win, then env (`PORT`, `WIDGETS_DEV_PORT`), then defaults 3000/3001. No retry. |
| Busy preferred port | probe result | `<paste output of scripts/probe_discovery_port.py>` |
| Well-known stubs | `nitrostack/transports/http.py` | `/.well-known/oauth-*` stubs return "not supported" when OAuth is not configured. |

## 4. Comparison
| Capability | TypeScript | Python | Gap? |
|---|---|---|---|
| Separate discovery HTTP server | yes | yes, OAuth only | `<yes/no>` |
| Preferred port | `port` | `<...>` | `<...>` |
| Retry on busy port | `autoRetry`, `maxRetries` | `<result of probe>` | `<...>` |
| Reports the bound port to the host tool | `<...>` | stderr marker | `<...>` |
| Consumed by Studio/CLI | `<...>` | `<...>` | `<...>` |

## 5. Conclusion
Pick one and delete the other.

**A. Close: covered or intentionally omitted.**
Python's `<mechanism>` already provides `<what consumers need>`. Port retry is
not needed because `<reason>`. No change.

**B. Open an implementation issue.**
Gap: `<one sentence>`. Proposed scope:
- add `<auto_retry / max_retries>` to `<OAuthService / OAuthModule.for_root>`
- report the bound port through `<existing marker>`
- tests: busy preferred port, retries exhausted, port reported
Out of scope: `<...>`