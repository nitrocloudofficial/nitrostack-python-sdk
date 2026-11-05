# NitroStack Python SDK

A Python-idiomatic port of the **NitroStack** Model Context Protocol (MCP) framework, enabling NestJS-like modular architecture, dependency injection, execution pipelines, background task processing, built-in authentication modules, and a diagnostic testing harness.

---

## Features

- **Nested Modular Architecture**: Group components cleanly with `@module`.
- **Dependency Injection**: Explicit class constructor DI with `DIContainer` and `@injectable(deps=[...])`.
- **Pipeline Interceptors**: Build guards, middleware, interceptors, pipes, and exception filters for tool execution.
- **Asynchronous Background Tasks**: Spawn background workers automatically for long-running tools.
- **Built-in Authentication**: Modules for API Keys, JWT verification, and OAuth 2.1 (featuring Protected Resource Metadata discovery servers).
- **In-Process Testing Harness**: Run unit and integration tests against modules without managing subprocesses or real network transports.
- **CLI Tooling (`nitrostack-py`)**: Scaffold apps (`init`), generate components (`generate`), pack deployable wheels (`pack`), upgrade/install dependencies, validate projects, auto-register servers with Claude (`register`), and run hot-reload development servers (`dev`).

---

## Installation

```bash
pip install nitrostack
```

To install local developer or test dependencies:
```bash
pip install -e .
```

---
### Security: eMudhra/emSign CA incident

A [security incident](https://discuss.python.org/t/information-for-python-users-regarding-emudhra-emsign-ca-certificate-security-incident/109576) affecting eMudhra intermediate certificates means older packaging tools can be man-in-the-middled. Use at least:

- pip 26.2.2
- uv 0.13.4
- certifi 2026.10.19 (this SDK now requires it)

Upgrade pip and uv in existing virtual environments, refresh lockfiles (`uv lock --upgrade-package certifi`), and rebuild container images. The official `python:3.11` and `3.12` Docker images keep an older pip, so upgrade it with the hash-pinned command from the advisory.

`nitrostack-py install` upgrades the pip in the project `.venv` to a hash-pinned 26.2.2 before installing anything, and refuses to run `uv` older than 0.13.4.


## Scaffolding a New Project (Recommended)

You can quickly scaffold a new project template using the interactive CLI tool:

```bash
nitrostack-py init
```

*(Or via Python: `python -m nitrostack.cli.main init`)*

The project name is optional on the command line. If omitted, the CLI asks for it next:

```bash
nitrostack-py init my-server --template python-starter
```

This launches an interactive prompt where you can:
1. **Project name** (if not passed as an argument). Default: `my-mcp-server`.
2. **Choose a template** by explicit name:
   - **python-starter**: A simple calculator server.
   - **python-pizzaz**: A pizza shop finder with maps and widgets.
   - **python-oauth**: A flight booking server demonstrating OAuth 2.1 authentication and guarded routes.
3. **Provide metadata**: Specify a custom description and author name.
4. **Install dependencies**: `Install dependencies: (Y/n)` — Enter or `Y` runs `npm install` in `src/widgets`; `n` skips it. `--skip-install` skips the prompt.

Optional port flags override the defaults (**3000** MCP, **3001** widgets):

```bash
nitrostack-py init my-server --template python-starter --port 4000 --widget 4001
nitrostack-py dev --port 4000 --widget 4001
nitrostack-py start --port 4000 --widget 4001
```

`init` and `pack` vendor this SDK into `vendor/nitrostack` and pin it with `[tool.uv.sources]`. The dependency name stays `nitrostack` in `pyproject.toml` and `requirements.txt`, so `nitrostack-py install` and NitroCloud still run `uv sync`. Do not put a `file:` URL in `[project].dependencies` — that breaks the cloud wheel metadata.

Once scaffolded, follow the next steps printed by the CLI to run your server, configure environment variables, and try it out.

---

## CLI (`nitrostack-py`)

The CLI is installed with the SDK (`nitrostack-py`, or `python -m nitrostack.cli.main`). Run `nitrostack-py --help` to list commands.

### Project lifecycle

```bash
nitrostack-py init my-server
nitrostack-py dev          # hot-reload development server
nitrostack-py start        # production server (no reload)
nitrostack-py register --name my-mcp-server --file app.py
```

### Generate components

Existing `tool` and `module` generators are unchanged. Additional generators create pipeline and service stubs that follow the current Python decorator/protocol APIs:

```bash
nitrostack-py generate tool add_numbers
nitrostack-py generate module payments
nitrostack-py generate guard MyGuard
nitrostack-py generate pipe Validation
nitrostack-py generate interceptor Transform
nitrostack-py generate filter HttpException
nitrostack-py generate service Email
```

Generated files:

| Command | Output |
|---|---|
| `generate tool <name>` | `{name}_tool.py` in the current directory |
| `generate module <name>` | `{name}_module.py` in the current directory |
| `generate guard <Name>` | `guards/<name>.py` |
| `generate pipe <Name>` | `pipes/<name>.py` |
| `generate interceptor <Name>` | `interceptors/<name>.py` |
| `generate filter <Name>` | `filters/<name>.py` |
| `generate service <Name>` | `services/<name>.py` |

Attach generated pipeline classes with `@use_guards`, `@use_pipes`, `@use_interceptors`, or `@use_filters`. Register services in a module's `providers` list.

### Pack a deployable wheel

```bash
nitrostack-py pack --dry-run    # list files; does not write an artifact
nitrostack-py pack              # write dist/*.whl
```

`pack` builds a wheel with setuptools (the same backend as this SDK), refreshes `requirements.txt` from `pyproject.toml` when possible, and always includes `.env.example`. The real `.env` file and other secrets are never packed. Temporary build directories are deleted afterwards.

### Upgrade, install, validate

```bash
nitrostack-py upgrade                 # latest nitrostack on PyPI (writes nitrostack>=latest)
nitrostack-py upgrade --version 0.3.2 # pin exactly this version (writes nitrostack==0.3.2)
nitrostack-py upgrade --dry-run       # print the change; do not edit files
nitrostack-py upgrade --allow-downgrade --version 0.1.0  # required to pin an older version

nitrostack-py install                 # install project + development dependencies
nitrostack-py install --production    # skip optional extras and requirements-dev.txt

nitrostack-py validate                # lint deps, @mcp_app imports, and @module() refs
```

`init` writes `pyproject.toml`, `.python-version`, and `uv.toml`, then runs `uv lock` when `uv` is on PATH. `install` prefers `uv sync` in that case. If `requirements.txt` pins nitrostack as a local path (`-e /path/to/nitrostack-python-sdk`), install uses `pip install -r requirements.txt` instead so unpublished SDK testing still works. The uv equivalent is:

```toml
[tool.uv.sources]
nitrostack = { path = "/path/to/nitrostack-python-sdk", editable = true }
```

then `uv lock` / `uv sync`. Without `uv`, install falls back to `.venv` + `pip`.

`upgrade` updates the `nitrostack` dependency spec in `pyproject.toml` in place (and `requirements.txt` when it already pins nitrostack). `--version X` writes `nitrostack==X`. Without `--version`, the latest PyPI release is written as `nitrostack>=latest`. A target older than the currently declared version is rejected unless `--allow-downgrade` is passed. `validate` reports missing/conflicting dependencies, `@mcp_app` modules that fail to import, and `@module()` `imports`/`exports` that are not real classes.

---

## Quick Start

### 1. Write your First Server

Create a file named `app.py`:

```python
import asyncio
from pydantic import BaseModel, Field
from nitrostack import (
    tool,
    resource,
    injectable,
    module,
    mcp_app,
    McpApplicationFactory,
    ServerConfig,
    ExecutionContext,
)

# 1. Input Validation Schema
class AddInput(BaseModel):
    a: float = Field(description="First number")
    b: float = Field(description="Second number")

# 2. Injected Provider Service
@injectable(deps=[])
class CalculatorService:
    def add(self, a: float, b: float) -> float:
        return a + b

# 3. Controller
@injectable(deps=[CalculatorService])
class CalculatorController:
    def __init__(self, service: CalculatorService):
        self.service = service

    @tool(
        name="add",
        description="Add two numbers together",
        input_schema=AddInput
    )
    async def add(self, input: AddInput, context: ExecutionContext) -> float:
        context.logger.info(f"Adding {input.a} and {input.b}")
        return self.service.add(input.a, input.b)

    @resource(
        uri="calc://info",
        name="Calculator Info",
        description="Metadata about this calculator"
    )
    async def get_info(self, context: ExecutionContext) -> str:
        return "Simple Add Calculator v1.0.0"

# 4. Modules
@module(
    name="calculator",
    controllers=[CalculatorController],
    providers=[CalculatorService]
)
class CalculatorModule:
    pass

@module(
    name="app",
    imports=[CalculatorModule]
)
class AppModule:
    pass

# 5. Application Entrypoint
@mcp_app(
    module=AppModule,
    server=ServerConfig(name="math-server", version="1.0.0")
)
class App:
    pass

async def main():
    app = await McpApplicationFactory.create(App)
    await app.start()

if __name__ == "__main__":
    asyncio.run(main())
```

### 2. Configure Environment Variables

The SDK reads standard settings from the environment or `.env` files:

| Environment Variable | Description |
|---|---|
| `HOST` | Bind address for HTTP/SSE (default: `127.0.0.1`). Set `0.0.0.0` to listen on all interfaces (containers). |
| `TRUSTED_PROXIES` / `MCP_TRUSTED_PROXIES` | Comma-separated IPs, CIDRs, or hostnames allowed to send `X-Forwarded-Host` / `X-Forwarded-Proto`. Unset means those headers are ignored. `X-Forwarded-For` is never used to decide trust. |
| `PORT` / `MCP_SERVER_PORT` | The port to bind for HTTP/SSE transport (default: `3000`). Overridden by `nitrostack-py --port`. |
| `WIDGETS_DEV_PORT` | Widget Next.js port (default: `3001`). Overridden by `nitrostack-py --widget`. |
| `MCP_TRANSPORT_TYPE` | Transport selection: `stdio`, `http`, or `dual` (combining stdio + HTTP/SSE). |
| `NODE_ENV` | If set to `production`, defaults to `dual` transport. Otherwise defaults to `stdio`. |
| `MCP_MAX_SESSIONS` | Cap on concurrent Streamable HTTP sessions; new sessions beyond the cap get an HTTP `429`. Unset = unlimited. |
| `MCP_SESSION_TIMEOUT_MS` | Idle timeout (ms) for stateful HTTP sessions; sessions with no activity for this long are terminated automatically. Unset = no timeout. |
| `MCP_GRACEFUL_SHUTDOWN_TIMEOUT_MS` | How long (ms) the HTTP transport waits for in-flight requests to finish when shutting down (default: `10000`). |
| `NITRO_MCP_PROTOCOL_VERSION` | Protocol era (case-insensitive): `auto` / `both` / `dual` / `dual-spec` (default when unset or unknown), `modern` / `latest` / `2026` / `2026-07-28`, or `legacy` / `2025` / `2025-06-18` / `2025-11-25`. `auto` is not the same as `modern`. Wins over `ServerConfig.protocol_era`. |
| `MCP_STATELESS` | Explicit override: `true` forces `modern` (stateless HTTP), `false` forces `legacy` (sessionful). Wins over `NITRO_MCP_PROTOCOL_VERSION` and `ServerConfig.protocol_era`. |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | Comma-separated allow-lists for DNS-rebinding protection, used only when CORS is disabled. |
| `NITROSTACK_LOG_FILE` | Destination file for logs (default: `nitrostack.log`). |
| `NITROSTACK_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `NITROSTACK_LOG_TO_STDOUT` | Set to `true` to allow logging to stdout under stdio transport (Caution: may corrupt protocol stream). |
| `NITROSTACK_HTTP_DEBUG` | Set to `1` to log every HTTP request (method, path, headers, body) and response status to stderr. Use when diagnosing a client that fails to connect: uvicorn's access log shows neither headers nor timestamps. |

### Keeping secrets out of logs (`SecretValue`)

Wrap a secret in `SecretValue` so it cannot end up in logs, `repr`, tracebacks, pickles or
serialized output. Everything prints `[SecretValue: REDACTED]`. The raw value is only available
through `unwrap()`.

```python
from nitrostack import SecretValue, JWTModule, ApiKeyModule

jwt_secret = SecretValue.from_env("JWT_SECRET")          # raises if the variable is not set
optional = SecretValue.from_env_optional("MAYBE_SECRET")  # None if it is not set

print(f"{jwt_secret}")       # [SecretValue: REDACTED]
JWTModule.for_root(secret=jwt_secret)
ApiKeyModule.for_root(keys=[SecretValue.from_env("SERVICE_API_KEY")])
```

- `SecretValue.from_value("...", allow_hardcoded=True, reason="test")` is for values written in code
  (tests). It must be allowed explicitly and it warns.
- Pickling and Pydantic/JSON output are redacted too. A pickled secret loads back with no value, and
  `unwrap()` raises instead of returning a fake one.
- `==` compares with `hmac.compare_digest`.
- `JWTModule`, `ApiKeyModule`, `OAuthModule` (the two client secrets) and `ConfigService`
  (`defaults`, `get_secret()`) accept a `SecretValue` or a plain `str`.
- `is_secret_value(x)` and `unwrap_secret(x)` help code that takes either.

---

### Quick auth setup

One call per auth type returns the keyword arguments for the matching module, and
`validate_auth_env` lists every missing environment variable at once:

```python
from nitrostack import (
    JWTModule, ApiKeyModule, OAuthModule,
    setup_jwt_auth, setup_api_key_auth, setup_oauth_auth,
    validate_auth_env, print_auth_setup_instructions,
)

ok, missing = validate_auth_env("oauth")      # (False, ["RESOURCE_URI", "AUTH_SERVER_URL", ...])
if not ok:
    print_auth_setup_instructions("oauth")

JWTModule.for_root(**setup_jwt_auth())         # reads JWT_SECRET
ApiKeyModule.for_root(**setup_api_key_auth())  # reads API_KEY / API_KEY_<n>
OAuthModule.for_root(**setup_oauth_auth())     # reads RESOURCE_URI, AUTH_SERVER_URL, JWKS_URI
```

`generate_test_credentials()` returns random `SecretValue` credentials for tests and local
demos only. They are never printed; call `.unwrap()` to read one.

## Transport Options

NitroStack apps can run over three transports, selected via `MCP_TRANSPORT_TYPE` (or `ServerConfig(transport_type=...)`):

- **`stdio`** (default outside production): JSON-RPC over stdin/stdout — the standard mode for desktop MCP clients (Claude Desktop, Cursor, etc.).
- **`http`**: Streamable HTTP + legacy SSE over a real network port, for cloud/remote deployments. Exposes:
  - `GET /` — documentation landing page (connection setup for Cursor/Claude/ChatGPT plus the registered tool catalog). Same page TypeScript NitroStack serves on Open URL.
  - `POST/GET/DELETE /mcp` — Streamable HTTP (session-based JSON-RPC + SSE streaming). `/mcp` and `/mcp/` are equivalent; the server does not 307 between them (MCP Inspector needs the no-slash URL for its SSE GET).
  - `GET /sse` + `POST /mcp/messages/` — legacy HTTP+SSE for older clients (trailing slash required so messages aren't swallowed by the Streamable HTTP `/mcp` mount)
  - `GET /mcp/health` — health check (`status`, active session count, uptime)
  - Per-session isolation, idle-session timeouts, and DNS-rebinding protection are provided by the underlying `mcp` SDK's `StreamableHTTPSessionManager`; NitroStack adds CORS, a concurrent-session cap, and the health endpoint on top.
  - Task-mode tools that call `context.task.update_progress(...)` push a live `notifications/progress` event over the session's SSE stream (in addition to always being pollable via `tasks/get`) whenever the client sends a `_meta.progressToken` on the `tools/call` request.
- **`dual`** (default in production): runs `stdio` and `http` concurrently as `asyncio` tasks in the same process/event loop — not separate threads — so both share the same `DIContainer` singletons, and uvicorn's signal-based graceful shutdown works correctly (it only installs signal handlers on the main thread). Shutdown is coordinated: either transport stopping (STDIO hitting EOF, or HTTP receiving a termination signal) cleanly stops the other.

Example:
```python
server = ServerConfig(name="my-server", transport_type="http", max_sessions=100, session_timeout_ms=1_800_000)
```

### Health resources

Decorated health checks are exposed at both `health://status` and `health://checks`.
The status resource keeps its original `{check_name: status}` JSON map for existing
clients. The checks resource returns an aggregate payload with `status`, `uptimeSeconds`,
per-check `name`, `status`, `latencyMs`, and `message`, plus `count`, `transforms`,
`timestamp`, and `sdkVersion`. Checks default to a five-second timeout and run independently;
set a per-check timeout with `@health_check("database", timeout=2.0)`. A false check result
marks the aggregate `degraded`; a timeout or exception marks it `unhealthy` (exceptions are
reported with a generic message on `health://checks`; the legacy status resource retains its
existing error string). A timed-out check has status `timeout`.

Async checks are timed out on the event loop. Sync checks run in a dedicated four-worker pool,
with at most four submitted sync checks at a time; a timed-out sync function keeps its worker
slot until it returns, but does not hold up the resource response or `asyncio.run()` shutdown.
Executor threads are non-daemon, so a sync check that never returns can still delay process exit.
Sync checks therefore run off the main thread and should not depend on thread-bound resources
such as a database connection created on the main thread. Transform entries currently report
their name, type, and order; `details` is reserved for telemetry that is not yet collected.
The SDK version is resolved once when the package loads.

`ServerHealthPayload` and `TransformTelemetry` are exported from `nitrostack` for typing.

`ServerConfig.protocol_era` is used only when `MCP_STATELESS` and `NITRO_MCP_PROTOCOL_VERSION` are both unset. Unknown tokens become `auto`, same as an unknown env value.

### Per-session tool visibility

Tools can declare `visibility="hidden"` and `default_visible=False`, then handlers can
call `context.enable_tools(["admin_tool"])` or `context.disable_tools([...])`. Overrides
are scoped by transport session ID and verified token subject, with a bounded TTL/LRU
store. Dynamic visibility requires a sessionful transport. The default `auto` era is
sessionless, so changes warn and do nothing there; the SDK does not fall back to
subject-only state because it would share overrides across that subject's sessions.

---

## Developing & Testing

### Auto-Registering with Claude Desktop
To automatically configure your server script with Claude Desktop without any manual editing:
```bash
nitrostack-py register --name my-mcp-server --file app.py
```
*(If your scripts folder is not in PATH, use: `python -m nitrostack.cli.main register --name my-mcp-server --file app.py`)*

This detects all standard and Windows Store installation directories, sets up virtualenv executables, and writes the JSON configuration. Once registered, simply restart Claude Desktop.

### Widgets (UI tools)

Applications with registered widgets expose an `application/json` manifest at
`widget://examples`. Discover it through MCP `resources/list` and read it with
`resources/read`, using `{"uri": "widget://examples"}` as the request parameters.
The manifest contains `widgets`, `count`, and `loaded`; each widget includes its
route, URI, mode-specific MIME type, metadata, associated tool names, and any
declared `ToolExamples` inputs and outputs. It is built at startup from registered
decorators, including helper-generated options, without executing tools.

For Python access:

```python
from nitrostack import get_widget_registry

registry = get_widget_registry(app)  # also available as app.widget_registry
widgets = registry.get_all_widgets()
widget_info = registry.get_widget("ui://widget/card.html")  # None if absent
manifest = registry.get_manifest().to_dict()
```

Each application owns its registry, and reads return detached copies. Shared
routes appear once in sorted order, retaining examples from every associated
tool. Applications without widgets expose no additional resource. In widget
applications, `widget://examples` is reserved; a conflicting resource registration
raises a startup error. Existing widget HTML resources and rendering are unchanged.

Bind a static HTML template to a tool with `@widget` and return domain JSON from the handler:

```python
from nitrostack import tool, widget, ExecutionContext

@tool(name="show_card", description="Product card", input_schema=CardInput)
@widget("card")
async def show_card(self, input: CardInput, context: ExecutionContext) -> dict:
    return {"name": "Widget", "price": 9.99}
```

Place HTML at `widgets/out/{route}.html` (e.g. `widgets/out/card.html`). The SDK registers
`ui://widget/card.html` as an MCP resource and sets mode-gated `_meta` on `tools/list` and
`tools/call` results.

**`NITROSTACK_APP_MODE`** (default `universal`):

| Mode | Tool `_meta` | Resource MIME |
|------|----------------|---------------|
| `universal` (default) | Both OpenAI and MCP Apps keys | `text/html;profile=mcp-app` |
| `openai` | `openai/outputTemplate`, `ui/template` | `text/html` |
| `mcp-app` | `_meta.ui` (`resourceUri`, `visibility`, CSP) | `text/html;profile=mcp-app` |

Object form for CSP and border options:

```python
from nitrostack import WidgetOptions, WidgetCsp, widget

@widget(WidgetOptions(
    route="chart",
    prefers_border=True,
    csp=WidgetCsp(connect_domains=["https://api.example.com"]),
))
```

`nitrostack-py init` copies `widgets/out/{route}.html` for every `@widget`. Widget HTML
is generated in Python from the tool's `structuredContent` (one iframe, N cards).

MCP Inspector: use **HTTP + stateless**, then the **Apps** tab. `tools/call` also embeds
the data-filled HTML. Do not use `widgets/preview.html` as the live result — that file
is a static helper. Live preview: `http://localhost:3000/widgets/preview`.

Turn **Authentication off** in Inspector. Pizzaz/starter have no OAuth. If Auth is on,
Inspector POSTs `/register` and you will see `Cannot POST /register` / `Unexpected token '<'`.
Connect Streamable HTTP to `http://localhost:3000/mcp` (no trailing slash).

For **open pizza shops only**, call `show_pizza_list` with `{"openNow": true}` or
`show_pizza_map` with `{"filter": "open_now"}`. Omitting those fields returns every shop,
including closed ones (Pizzeria Delfina).

**NitroStudio:** folder-connect looks for a TypeScript project (`package.json` with
`@nitrostack/core` and `src/index.ts`). A Python server will not detect. Keep using
MCP Inspector over HTTP, or point Studio at a custom Streamable HTTP URL if the build
supports it. Do not enable OAuth against this server.

Example server: `examples/widgets_example.py` with templates in `examples/widgets/out/`.
For MCP Inspector over HTTP, use stateless mode:

```bash
cd examples
MCP_TRANSPORT_TYPE=http MCP_STATELESS=true NITROSTACK_APP_MODE=universal python widgets_example.py
```

### OAuth client

Use `OAuth2Client` for authorization-code/PKCE and client-credentials flows,
managed token storage, refresh, discovery, registration and revocation. Tokens
stay in memory unless a file store is explicitly configured. See the
[OAuth client guide](docs/oauth-client.md) for examples and security responsibilities.

### Running Tests
To run the automated test suite, execute:
```bash
python tests/test_basic.py
python tests/test_tasks.py
python tests/test_initial_tool.py
python tests/test_transports.py
python tests/test_widgets.py
python tests/test_widget_metadata.py
python tests/test_pizzaz_widgets.py
python tests/test_template_widgets.py
python tests/test_cli.py
pytest tests/test_cli.py -v
python tests/test_tool_input_schema.py
```

### Testing Harness
Write in-process unit tests using the harness:
```python
import asyncio
from nitrostack.testing import NitroTestingModule
from app import AppModule

async def test_add():
    harness = await NitroTestingModule.create(AppModule)
    result = await harness.call_tool("add", {"input": {"a": 5, "b": 10}})
    assert result == 15.0
    print("Test passed!")

if __name__ == "__main__":
    asyncio.run(test_add())
```

#### Overrides, cleanup and mocks
Replace a provider with a fake, and release resources when the test ends:
<!-- readme-test -->
```python
import asyncio
from pydantic import BaseModel
from nitrostack import ExecutionContext, module, injectable, tool
from nitrostack.testing import NitroTestingModule, MockLogger, create_mock_context


class ItemInput(BaseModel):
    key: str


@injectable()
class Database:
    def __init__(self):
        raise RuntimeError("the real database must not be built in tests")


class FakeDatabase:
    closed = 0

    def get(self, key):
        return f"fake-{key}"

    def on_module_destroy(self):  # provider shutdown hook; runs once on close()
        FakeDatabase.closed += 1


@injectable(deps=[Database])
class Items:
    def __init__(self, db):
        self.db = db

    @tool(name="get_item", description="Look up an item", input_schema=ItemInput)
    async def get_item(self, input: ItemInput, context: ExecutionContext) -> str:
        return self.db.get(input.key)


@module(name="items", providers=[Database, Items])
class AppModule:
    pass


async def test_get_item():
    async with await NitroTestingModule.create(AppModule, overrides={Database: FakeDatabase()}) as app:
        assert await app.call_tool("get_item", {"input": {"key": "a"}}) == "fake-a"
    assert FakeDatabase.closed == 1  # close() ran the hook; calling it again would not repeat it

    logger = MockLogger()
    context = create_mock_context(logger=logger)
    context.logger.info("hello")
    assert logger.has_log("info", "hello")


if __name__ == "__main__":
    asyncio.run(test_get_item())
```
`overrides` takes an instance, or a class/function that builds one (it may be `async`). Overrides
are undone by `close()`. Shutdown hooks are methods named `on_module_destroy` (sync or async).
`flush_tasks()` yields to the event loop so background tasks can finish. The optional pytest
fixture `nitro_client` is enabled with `pytest_plugins = ["nitrostack.testing.pytest_plugin"]`.

### Large tool results: data spillover

Attach `DataSpilloverInterceptor` to a tool to store large results separately and
return a preview, `resource_uri`, and `original_size_bytes`. Results at or below
the threshold pass through unchanged. Sizes count serialized UTF-8 bytes, not
characters; lists and dictionaries use compact JSON.

```python
from pydantic import BaseModel
from nitrostack import DataSpilloverInterceptor, MemorySpilloverStore, tool, use_interceptors

spillover = DataSpilloverInterceptor(
    threshold_bytes=64 * 1024,
    store=MemorySpilloverStore(
        max_bytes=10 * 1024 * 1024,
        max_total_bytes=100 * 1024 * 1024,
        ttl_seconds=300,
    ),
    resource_uri_prefix="resource://data-spillover/",
)

class ReportInput(BaseModel):
    count: int = 10000

class ReportTools:
    @tool(name="report", description="Generate a report", input_schema=ReportInput)
    @use_interceptors(spillover)
    async def report(self, input: ReportInput, context):
        return [{"row": index} for index in range(input.count)]
```

Register `ReportTools` as a module provider or controller as usual. A configured
interceptor instance works directly in the decorator. For default configuration,
`@use_interceptors(DataSpilloverInterceptor)` also works through dependency injection.

The MCP result includes a text preview, structured content, and a resource link.
Clients retrieve the full result with `resources/read`, for example:

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "resources/read",
  "params": {"uri": "resource://data-spillover/<id-from-tool-result>"}
}
```

Lists and dictionaries preview their first five items or entries and total counts.
Strings preview up to 512 characters. The complete preview stays within 2 KiB;
large nested values are represented as truncated text. Reading the full resource
can consume context again, so clients should retrieve it only when needed.

For disk storage, replace the store with:

```python
from nitrostack import FileSpilloverStore

store = FileSpilloverStore(
    ".nitrostack/spillover",
    max_bytes=10 * 1024 * 1024,
    max_total_bytes=100 * 1024 * 1024,
    ttl_seconds=300,
)
```

The memory store evicts least recently used results when its payload capacity is
full. The file store rejects writes when its capacity is full; its capacity counts
actual JSON file bytes, including base64 and metadata overhead. A single result
larger than `max_bytes` raises `PayloadTooLargeError`. Both stores remove expired
results during operations; file storage also cleans up on initialization. Expiry
is absolute and reads do not extend it. Each file-store root must be owned by one
store instance in one process; shared storage across processes is not supported.
Keep the configured directory private to the server account.

Resource access requires the same verified principal, issuer, client identity,
and tenant. Anonymous access is limited to the same server-side MCP session.
Requests without either a verified identity or trusted session cannot spill
results; invalid credentials do not fall back to anonymous access. Anonymous
HTTP clients need a persistent MCP session; sessionless HTTP clients should
authenticate. Expired results and results owned by another caller return
resource-not-found. Stored results are not enumerated in `resources/list`.

HTTP ownership uses the server-issued session ID after the official transport
has validated it, rather than the identity of an SDK request/session proxy.
Stdio assigns a random identity to each live stream connection. Requests on that
connection can retrieve its anonymous results; a later connection cannot inherit
them. Concurrent HTTP requests cannot borrow a stdio connection's identity.

Successful explicit MCP tool results are stored as JSON; errors and input-required
responses pass through. Decorated tools with output schemas advertise either the
original output or the spillover envelope. Spilled widget results return the
resource reference without invoking the widget's data transformer.

Independent HTTP/stdio tests and reproducible large-output checks live in
`tests/test_spillover_transport.py`. Run the spillover tests with
`python -m pytest tests/test_spillover.py tests/test_spillover_transport.py`.

### Progressive discovery and code-mode templates

| Template | Example |
| --- | --- |
| `python-enterprise-search` | BM25 discovery over local finance, inventory and support tools |
| `python-code-mode` | Bounded JavaScript workflows calling Python tools through native QuickJS |

```sh
nitrostack-py init search-demo --template python-enterprise-search --skip-install
nitrostack-py init code-demo --template python-code-mode --skip-install
```

Each generated README explains installation, tool calls, validation, upgrade and
packaging. Both use deterministic sample data without credentials or external
services. Code-mode execution additionally requires `quickjs>=1.19.4`; its workers
start lazily and are disposed on graceful shutdown. Existing starter, pizzaz and
OAuth templates remain available.
