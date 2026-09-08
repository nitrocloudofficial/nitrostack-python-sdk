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

---

## Transport Options

NitroStack apps can run over three transports, selected via `MCP_TRANSPORT_TYPE` (or `ServerConfig(transport_type=...)`):

- **`stdio`** (default outside production): JSON-RPC over stdin/stdout — the standard mode for desktop MCP clients (Claude Desktop, Cursor, etc.).
- **`http`**: Streamable HTTP + legacy SSE over a real network port, for cloud/remote deployments. Exposes:
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

## Developing & Testing

### Auto-Registering with Claude Desktop
To automatically configure your server script with Claude Desktop without any manual editing:
```bash
nitrostack-py register --name my-mcp-server --file app.py
```
*(If your scripts folder is not in PATH, use: `python -m nitrostack.cli.main register --name my-mcp-server --file app.py`)*

This detects all standard and Windows Store installation directories, sets up virtualenv executables, and writes the JSON configuration. Once registered, simply restart Claude Desktop.

### Widgets (UI tools)

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
