# NitroStack Python SDK

<a href="https://www.producthunt.com/products/nitrostack?embed=true&amp;utm_source=badge-top-post-badge&amp;utm_medium=badge&amp;utm_campaign=badge-nitrostack" target="_blank" rel="noopener noreferrer"><img alt="NitroStack - Build, test, and deploy MCP servers in one place | Product Hunt" width="250" height="54" src="https://api.producthunt.com/widgets/embed-image/v1/top-post-badge.svg?post_id=1273961&amp;theme=neutral&amp;period=daily&amp;t=1792301273013"></a>

Build Model Context Protocol (MCP) servers in Python with a NestJS-style structure: modules, dependency injection, decorators for tools, resources and prompts, and a pipeline of guards, middleware, interceptors, pipes and exception filters. It's the Python port of the [NitroStack](https://docs.nitrostack.ai/) TypeScript framework.

- **Docs:** [docs.nitrostack.ai/python](https://docs.nitrostack.ai/python)
- **Source and issues:** [github.com/nitrocloudofficial/nitrostack-python-sdk](https://github.com/nitrocloudofficial/nitrostack-python-sdk)
- **Requires:** Python 3.10+

---

## Features

- **Modules and dependency injection**: group code with `@module`, inject services with `@injectable(deps=[...])`.
- **Tools, resources and prompts**: plain decorated methods, with Pydantic input/output schemas, tool annotations and examples.
- **Execution pipeline**: guards, middleware, interceptors, pipes and exception filters per tool.
- **Auth**: API keys, JWT, and OAuth 2.1 (protected resource metadata, token introspection or JWKS, PKCE, CIMD, scopes).
- **Long-running tasks**: tools that report progress and can be cancelled, over the MCP tasks API.
- **Widgets**: attach an HTML UI to a tool for ChatGPT Apps and MCP Apps clients.
- **Transports**: stdio, Streamable HTTP (plus legacy SSE), or both at once; 2025 and 2026 protocol versions.
- **Caching, rate limits, events and health checks** as decorators.
- **Testing harness**: call tools, resources and prompts in-process, without a transport.
- **CLI (`nitrostack-py`)**: scaffold, generate code, run with hot reload, validate, pack wheels, upgrade.
- **NitroStudio**: open a project folder in [NitroStudio](https://nitrostack.ai/) to run, chat with and debug it.

---

## Installation

```bash
pip install nitrostack
# or
uv add nitrostack
```

To work on the SDK itself (tests and tooling):

```bash
pip install -e ".[dev]"
```

---

## Scaffolding a New Project (Recommended)

```bash
nitrostack-py init my-server --template python-starter
```

*(Or via Python: `python -m nitrostack.cli.main init`)*

Both arguments are optional; without them `init` asks:

1. **Project name**. Default: `my-mcp-server`.
2. **Template**:

   | Template | What it shows |
   |---|---|
   | `python-starter` | A calculator: tools, a widget, a health check. |
   | `python-pizzaz` | A pizza shop finder with map and list widgets. |
   | `python-oauth` | Flight booking with OAuth 2.1 and scope-guarded tools (see `OAUTH_SETUP.md` in the project). |

3. **Description and author**.
4. **Install dependencies (Y/n)**: installs the Python dependencies (`uv sync` when `uv` is on PATH, otherwise `.venv` + `pip`), then runs `npm install` in `src/widgets` if the template has one. `--skip-install` skips this.

`init` also installs agent skills for this SDK (from [skills-python-sdk](https://github.com/nitrocloudofficial/skills-python-sdk)) into the project's coding-agent folders. `--force` overwrites existing skill folders.

Port flags override the defaults (**3000** for MCP, **3001** for widgets):

```bash
nitrostack-py init my-server --template python-starter --port 4000 --widget 4001
nitrostack-py dev --port 4000 --widget 4001
nitrostack-py start --port 4000 --widget 4001
```

`init` and `pack` vendor this SDK into `vendor/nitrostack` and pin it with `[tool.uv.sources]`. The dependency name stays `nitrostack` in `pyproject.toml` and `requirements.txt`, so `nitrostack-py install` and NitroCloud still run `uv sync`. Do not put a `file:` URL in `[project].dependencies`; that breaks the cloud wheel metadata. The project runs that vendored copy, whatever the version spec says; `nitrostack-py upgrade` replaces it with another release.

Once scaffolded, follow the next steps the CLI prints.

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

Run it with `python app.py` (stdio), or `MCP_TRANSPORT_TYPE=http python app.py` for Streamable HTTP on `http://127.0.0.1:3000/mcp`.

Scaffolded projects split the same pieces into files: `main.py` calls `McpApplicationFactory.create(AppModule)`, `app_module.py` holds the root module, and each feature lives in `modules/<name>/`. When the server's name is generic (`app` or `mcp-server`, as with a root `@module(name="app")` and no `@mcp_app`), the name and description reported to clients come from `SERVER_NAME` / `SERVER_DESC`.

### 2. Configure Environment Variables

The SDK reads settings from the process environment and the project's `.env` file. **The process environment wins:** a variable already set when the server starts (for example `PORT=3001` from NitroStudio or your shell) is not overwritten by `.env`. `.env` fills in what isn't set, and `ConfigModule.for_root(defaults=...)` fills in the rest. (Before 0.4.1, `.env` overwrote the environment.)

| Environment Variable | Description |
|---|---|
| `HOST` | Bind address for HTTP/SSE (default: `127.0.0.1`). Set `0.0.0.0` to listen on all interfaces (containers). |
| `TRUSTED_PROXIES` / `MCP_TRUSTED_PROXIES` | Comma-separated IPs, CIDRs, or hostnames allowed to send `X-Forwarded-Host` / `X-Forwarded-Proto`. Unset means those headers are ignored. `X-Forwarded-For` is never used to decide trust. |
| `PORT` / `MCP_SERVER_PORT` | The port to bind for HTTP/SSE transport (default: `3000`). Overridden by `nitrostack-py --port`. |
| `WIDGETS_DEV_PORT` | Widget Next.js port (default: `3001`). Overridden by `nitrostack-py --widget`. |
| `SERVER_NAME` / `SERVER_DESC` | Server name and description reported to clients when the configured name is generic (`app`, `mcp-server`). `init` sets `SERVER_NAME` to the project folder name. |
| `MCP_TRANSPORT_TYPE` | Transport selection: `stdio`, `http`, or `dual` (combining stdio + HTTP/SSE). |
| `NODE_ENV` | If set to `production`, defaults to `dual` transport. Otherwise defaults to `stdio`. |
| `MCP_MAX_SESSIONS` | Cap on concurrent Streamable HTTP sessions; new sessions beyond the cap get an HTTP `429`. Unset = unlimited. |
| `MCP_SESSION_TIMEOUT_MS` | Idle timeout (ms) for stateful HTTP sessions; sessions with no activity for this long are terminated automatically. Unset = no timeout. |
| `MCP_GRACEFUL_SHUTDOWN_TIMEOUT_MS` | How long (ms) the HTTP transport waits for in-flight requests to finish when shutting down (default: `10000`). |
| `NITRO_MCP_PROTOCOL_VERSION` | Protocol era (case-insensitive): `auto` / `both` / `dual` / `dual-spec` (default when unset or unknown), `modern` / `latest` / `2026` / `2026-07-28`, or `legacy` / `2025` / `2025-06-18` / `2025-11-25`. `auto` is not the same as `modern`. Wins over `ServerConfig.protocol_era`. |
| `MCP_STATELESS` | Explicit override: `true` forces `modern` (stateless HTTP), `false` forces `legacy` (sessionful). Wins over `NITRO_MCP_PROTOCOL_VERSION` and `ServerConfig.protocol_era`. |
| `MCP_ALLOWED_HOSTS` / `MCP_ALLOWED_ORIGINS` | Comma-separated allow-lists for DNS-rebinding protection, used only when CORS is disabled. |
| `NITROSTACK_APP_MODE` | Widget metadata flavour: `universal` (default), `openai`, or `mcp-app`. See [Widgets](#widgets-ui-tools). |
| `OAUTH_REQUIRED` | With `OAuthGuard`: `true` rejects calls without a valid token. Unset lets token-less calls through, for local development. |
| `NITROSTACK_LOG_FILE` | Destination file for logs (default: `nitrostack.log`). |
| `NITROSTACK_LOG_LEVEL` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `NITROSTACK_LOG_TO_STDOUT` | Set to `true` to allow logging to stdout under stdio transport (Caution: may corrupt protocol stream). |
| `NITROSTACK_HTTP_DEBUG` | Set to `1` to log every HTTP request (method, path, headers, body) and response status to stderr. Use when diagnosing a client that fails to connect: uvicorn's access log shows neither headers nor timestamps. |

Read your own settings through `ConfigService`:

```python
from nitrostack import ConfigModule, ConfigService, injectable, module

@injectable(deps=[ConfigService])
class WeatherService:
    def __init__(self, config: ConfigService):
        self.units = config.get("WEATHER_UNITS", "metric")
        self.api_key = config.get_or_throw("WEATHER_API_KEY")  # KeyError if unset

@module(
    name="app",
    imports=[ConfigModule.for_root(env_file_path=".env", defaults={"WEATHER_UNITS": "metric"})],
    providers=[WeatherService],
)
class AppModule:
    pass
```

---

## Building Blocks

The examples below build on each other (later ones reuse `LookupInput` and `NoInput`) and are methods on a controller (an `@injectable` class listed in a module's `controllers`). Every tool takes `(self, input, context)`, where `input` is an instance of its `input_schema` model and `context` is the `ExecutionContext` (logger, auth, task, request metadata).

### Tools

```python
from pydantic import BaseModel, Field
from nitrostack import tool, initial_tool, ToolAnnotations, ToolExamples, ExecutionContext

class LookupInput(BaseModel):
    city: str = Field(description="City name")

class Weather(BaseModel):
    city: str
    temp: float
    units: str

class NoInput(BaseModel):
    pass

class WeatherController:
    @tool(
        name="get_weather",
        title="Get Weather",
        description="Current weather for a city",
        input_schema=LookupInput,
        output_schema=Weather,  # optional; adds structuredContent
        annotations=ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True),
        examples=ToolExamples(input={"city": "Paris"}, output={"city": "Paris", "temp": 21, "units": "metric"}),
    )
    async def get_weather(self, input: LookupInput, context: ExecutionContext) -> dict:
        return {"city": input.city, "temp": 21, "units": "metric"}

    @tool(name="whats_new", description="Shown when a client connects", input_schema=NoInput)
    @initial_tool  # clients that support it call this on connect
    async def whats_new(self, input: NoInput, context: ExecutionContext) -> str:
        return "Try get_weather."
```

A tool with no arguments still needs an input model; use an empty `BaseModel` subclass like `NoInput` (not `BaseModel` itself). `visibility="hidden"` keeps a tool callable but out of `tools/list`. Raise an exception to return an MCP error to the client.

### Resources and prompts

```python
from typing import List
from nitrostack import resource, prompt, PromptArgument, PromptMessage, ExecutionContext

class WeatherController:
    @resource(uri="weather://units", name="Units", description="Units in use", mime_type="text/plain")
    async def units(self, context: ExecutionContext) -> str:
        return "metric"

    @prompt(
        name="forecast_brief",
        description="Write a short forecast",
        arguments=[PromptArgument(name="city", description="City", required=True)],
    )
    async def forecast_brief(self, args: dict, context: ExecutionContext) -> List[PromptMessage]:
        return [PromptMessage(role="user", content=f"Write a short forecast for {args['city']}.")]
```

### Guards, interceptors, pipes, middleware and filters

Each is a small class with one async method; attach it with a decorator below `@tool`. Generate stubs with `nitrostack-py generate guard|pipe|interceptor|filter <Name>`.

| Stage | Method | Attach with | Runs |
|---|---|---|---|
| Guard | `can_activate(context) -> bool` | `@use_guards(...)` | first; `False` rejects the call ("Access denied by guard") |
| Pipe | `transform(value, metadata)` | `@use_pipes(...)` | on the tool input, after guards |
| Middleware | `use(context, next_fn)` | `@use_middleware(...)` | around interceptors and the tool |
| Interceptor | `intercept(context, next_fn)` | `@use_interceptors(...)` | around the tool; can change its result |
| Exception filter | `catch(error, context)` | `@use_filters(...)` | on errors; its return value becomes the result |

```python
from nitrostack import tool, use_guards, use_interceptors, use_filters, ExecutionContext

class OfficeHoursGuard:
    async def can_activate(self, context: ExecutionContext) -> bool:
        return context.metadata.get("office_open", True)

class TimingInterceptor:
    async def intercept(self, context: ExecutionContext, next_fn):
        result = await next_fn()
        context.logger.info(f"{context.tool_name} finished")
        return result

class FriendlyErrors:
    async def catch(self, error: Exception, context: ExecutionContext):
        return {"error": str(error)}

class WeatherController:
    @tool(name="get_forecast", description="Forecast for a city", input_schema=LookupInput)
    @use_guards(OfficeHoursGuard)
    @use_interceptors(TimingInterceptor)
    @use_filters(FriendlyErrors)
    async def get_forecast(self, input: LookupInput, context: ExecutionContext) -> dict:
        return {"city": input.city, "forecast": "sunny"}
```

### Authentication

Register an auth module in the root module, then guard tools. Guards fill `context.auth` (`subject`, `scopes`, `client_id`, `claims`, ...).

| Module | Guard | Configured by |
|---|---|---|
| `ApiKeyModule.for_root(keys_env_prefix="API_KEY", hashed=False)` | `ApiKeyGuard` | `x-api-key` header checked against `API_KEY`, `API_KEY_1`, `API_KEY_2`, ... (SHA-256 hashes when `hashed=True`) |
| `JWTModule.for_root(secret_env_var="JWT_SECRET", audience=..., issuer=...)` | `JwtGuard` | `Authorization: Bearer <jwt>` |
| `OAuthModule.for_root(resource_uri=..., authorization_servers=[...], scopes_supported=[...], token_introspection_endpoint=... or jwks_uri=...)` | `OAuthGuard` | OAuth 2.1 access tokens, audience-bound; `OAUTH_REQUIRED=true` to require one |

```python
from nitrostack import (
    ApiKeyGuard, ApiKeyModule, ExecutionContext, OAuthGuard, module, require_scopes, tool, use_guards,
)

class AccountController:
    @tool(name="get_balance", description="Account balance", input_schema=NoInput)
    @use_guards(ApiKeyGuard)
    async def get_balance(self, input: NoInput, context: ExecutionContext) -> dict:
        return {"balance": 42}

    @tool(name="close_account", description="Close the account", input_schema=NoInput)
    @use_guards(OAuthGuard)
    @require_scopes("admin")  # needs context.auth with every listed scope
    async def close_account(self, input: NoInput, context: ExecutionContext) -> dict:
        return {"closed": True}

@module(name="app", imports=[ApiKeyModule.for_root()], controllers=[AccountController])
class AppModule:
    pass
```

Register `ApiKeyModule` when you use `ApiKeyGuard`: without it, the guard falls back to the `API_KEY` variable, and with neither set it lets every call through.

`has_scope`, `has_any_scope` and `has_all_scopes` check `context.auth` inside a tool. For OAuth, start from the `python-oauth` template and its `OAUTH_SETUP.md`; PKCE (`generate_pkce_params`, `verify_pkce`), client ID metadata documents (`resolve_cimd`) and `generate_www_authenticate_header` are exported for custom flows.

### Caching and rate limits

```python
from nitrostack import cache, injectable, rate_limit, tool

@injectable()
class WeatherService:
    @cache(ttl=60)  # seconds; keyed on the arguments
    async def lookup(self, city: str) -> dict:
        return {"city": city, "temp": 21}

class WeatherController:
    @tool(name="ping", description="Ping", input_schema=NoInput)
    @rate_limit(max=10, window=60)  # 10 calls per 60 s, across all callers
    async def ping(self, input: NoInput, context: ExecutionContext) -> str:
        return "pong"
```

Put `@cache` on a service method with plain, hashable arguments (strings, numbers, tuples). It can't key on a tool's Pydantic input object.

### Long-running tasks

Set `task_support="optional"` (or `"required"`) and the client can run the tool as an MCP task: it polls `tasks/get`, can cancel it, and gets live `notifications/progress` when it sends a `progressToken`. `context.task` is `None` when the tool is called normally.

```python
import asyncio
from pydantic import BaseModel
from nitrostack import tool, ExecutionContext

class ReportInput(BaseModel):
    steps: int = 5

class ReportController:
    @tool(name="build_report", description="Build a report", input_schema=ReportInput, task_support="optional")
    async def build_report(self, input: ReportInput, context: ExecutionContext) -> dict:
        for step in range(1, input.steps + 1):
            if context.task:
                context.task.update_progress(f"Step {step}/{input.steps}")
                context.task.throw_if_cancelled()
            await asyncio.sleep(1)
        return {"steps": input.steps}
```

### Events and health checks

```python
from typing import List
from nitrostack import EventEmitter, health_check, injectable, on_event

@injectable()
class OrderService:
    async def place(self, order_id: str) -> None:
        await EventEmitter.get_instance().emit("order.placed", {"id": order_id})

@injectable()
class AuditLog:
    def __init__(self):
        self.entries: List[str] = []

    @on_event("order.placed")
    def record(self, payload: dict) -> None:
        self.entries.append(payload["id"])

    @health_check("audit")
    def healthy(self) -> bool:
        return True
```

Listeners and health checks are found on any provider or controller in a module. When any health check exists, the server exposes the `health://status` resource (`{"audit": "healthy"}`). Keep health checks synchronous.

---

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

`ServerConfig.protocol_era` is used only when `MCP_STATELESS` and `NITRO_MCP_PROTOCOL_VERSION` are both unset. Unknown tokens become `auto`, same as an unknown env value.

---

## CLI (`nitrostack-py`)

The CLI is installed with the SDK (`nitrostack-py`, or `python -m nitrostack.cli.main`). Run `nitrostack-py --help` to list commands.

### Project lifecycle

```bash
nitrostack-py init my-server
nitrostack-py dev          # hot-reload development server
nitrostack-py start        # production server (no reload)
nitrostack-py register --name my-mcp-server --file main.py
```

### Generate components

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
nitrostack-py pack --include-env  # also pack .env files (use with caution!)
```

`pack` builds a wheel with setuptools (the same backend as this SDK), refreshes `requirements.txt` from `pyproject.toml` when possible, and always includes `.env.example`. `.env`, `.env.local`, and `.env.production` are excluded unless you pass `--include-env`. Temporary build directories are deleted afterwards.

### Upgrade, install, validate

```bash
nitrostack-py upgrade                 # move the project to the latest nitrostack on PyPI
nitrostack-py upgrade --version 0.4.1 # move it to exactly this version
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

`upgrade` in a project with `vendor/nitrostack` (every project `init` creates) replaces the vendored copy with the target release: the wheel is downloaded from PyPI and checked against its published sha256, or copied from the running CLI when it is that release. The dependency stays the plain name `nitrostack`. In other projects, `upgrade` updates the `nitrostack` spec in `pyproject.toml`'s `dependencies` (and `requirements.txt` when it already pins nitrostack): `--version X` writes `nitrostack==X`, and without `--version` the latest release is written as `nitrostack>=latest`. Either way, run `nitrostack-py install` afterwards, and a target older than the current version is rejected unless `--allow-downgrade` is passed. `validate` reports missing/conflicting dependencies, `@mcp_app` modules that fail to import, and `@module()` `imports`/`exports` that are not real classes.

---

## Widgets (UI tools)

Bind an HTML template to a tool with `@widget` and return domain JSON from the handler:

```python
from nitrostack import tool, widget, ExecutionContext

class ShopController:
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
async def show_chart(self, input, context):
    ...
```

`nitrostack-py init` copies `widgets/out/{route}.html` for every `@widget`. Widget HTML
is generated in Python from the tool's `structuredContent` (one iframe, N cards).

MCP Inspector: use **HTTP + stateless**, then the **Apps** tab. `tools/call` also embeds
the data-filled HTML. Do not use `widgets/preview.html` as the live result — that file
is a static helper. Live preview: `http://localhost:3000/widgets/preview`.

Turn **Authentication off** in Inspector for the starter and pizzaz templates (they have no OAuth). If Auth is on,
Inspector POSTs `/register` and you will see `Cannot POST /register` / `Unexpected token '<'`.
Connect Streamable HTTP to `http://localhost:3000/mcp` (no trailing slash).

For **open pizza shops only**, call `show_pizza_list` with `{"openNow": true}` or
`show_pizza_map` with `{"filter": "open_now"}`. Omitting those fields returns every shop,
including closed ones (Pizzeria Delfina).

Example server: `examples/widgets_example.py` with templates in `examples/widgets/out/`.
For MCP Inspector over HTTP, use stateless mode:

```bash
cd examples
MCP_TRANSPORT_TYPE=http MCP_STATELESS=true NITROSTACK_APP_MODE=universal python widgets_example.py
```

---

## Using with Clients

### NitroStudio

Open the project folder in NitroStudio. It recognises a Python NitroStack project by `main.py` plus `nitrostack` in `requirements.txt` or `pyproject.toml`, starts it, and lets you call tools, preview widgets, chat with it, and run OAuth. Studio passes the port it picks through `PORT`, which wins over `.env` (0.4.1 and later), so several projects can run side by side.

### Claude Desktop

```bash
nitrostack-py register --name my-mcp-server --file main.py
```

*(If your scripts folder is not in PATH, use: `python -m nitrostack.cli.main register --name my-mcp-server --file main.py`)*

This finds the Claude Desktop config (standard and Windows Store installs), points it at your virtualenv's Python, and writes the server entry. Restart Claude Desktop afterwards.

---

## Testing

### Testing Harness

Call tools, resources and prompts in-process with `NitroTestingModule`; no transport, no subprocess:

```python
import asyncio
from nitrostack.testing import NitroTestingModule
from app import AppModule

async def test_add():
    harness = await NitroTestingModule.create(AppModule)
    assert await harness.call_tool("add", {"a": 5, "b": 10}) == 15.0
    assert await harness.read_resource("calc://info") == "Simple Add Calculator v1.0.0"

if __name__ == "__main__":
    asyncio.run(test_add())
```

`call_tool` takes the tool's arguments as an MCP client sends them and returns the decoded result (JSON is parsed; errors come back as their message). `get_prompt(name, args)` returns the prompt messages.

### Running the SDK's tests

```bash
pip install -e ".[dev]"
pytest tests
```
