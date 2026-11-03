# Code mode

Compose several Python tool calls in one JavaScript workflow using native
QuickJS inside isolated worker processes. This Python implementation does not
use WebAssembly.
## Run the generated project

```sh
nitrostack-py install
nitrostack-py validate
python main.py
```

`python main.py` serves MCP over stdio: connect it to an MCP client rather than
expecting a browser page. For HTTP set `MCP_TRANSPORT_TYPE=http` in your shell,
then run `python main.py` and connect to `http://localhost:3000/mcp`.
On PowerShell use `$env:MCP_TRANSPORT_TYPE="http"`; on a POSIX shell use
`MCP_TRANSPORT_TYPE=http python main.py`. Do not expose this unauthenticated demo
on a public interface. No credentials or external services are required.

The CLI creates `.env` from `.env.example`; adjust it for optional configuration.
Every finance, inventory and support record is fixed demonstration data.
The code supports Python 3.10+; `.python-version` selects 3.12 for uv.

## Discover and execute

The exposed catalog is `search`, `get_schema`, and `execute`. The seven underlying
read-only finance, inventory, support and numeric tools remain discoverable.

1. Call `search` with `{ "query": "inventory" }` (or an empty query to browse).
2. Call `get_schema` with `{ "name": "inventory_check_stock" }`.
3. Read `scripts/sample-batch.js` and pass its text as the `code` argument to
   `execute`. The script checks two SKUs and sums their quantities:

```javascript
const stock = ["keyboard", "monitor"].map(sku =>
    tools.call("inventory_check_stock", {sku})
);
const summary = tools.call("data_aggregate_sum", {values: stock.map(item => item.quantity)});
return {stock, total_units: summary.total};
```

The successful execution result has `success: true`, `value.total_units: 18`,
and `toolCallCount: 3`. `tools.call` is synchronous. Scripts are function bodies
and may use `return`; Node.js modules, `fetch`, browser APIs and top-level `await`
are unavailable. Python handles each tool invocation through the SDK pipeline.

## Installation and limits

Both dependency files declare `nitrostack` and `quickjs>=1.19.4`. Install the
project dependencies before executing scripts. Native QuickJS wheel availability
varies by Python/platform; a source install may require a C build toolchain.
If your Windows environment cannot build it, use a supported Linux/WSL environment.
Generation, import, discovery and validation do not create workers; the first
`execute` initializes the pool lazily.

`main.py` sets a 15-second timeout, 100 MiB guest memory limit, 20 tool calls,
one worker process and `allow_destructive=False`. Tools explicitly declare
read-only annotations. Execution is bounded by the SDK, but these process and
runtime limits are not a complete security boundary for arbitrary hostile code.
Run only trusted demonstration workflows. Resource limits do not replace tool
permissions or authentication.

The entry point awaits `CODE_MODE.dispose()` in `finally`, including failed
startup, so workers are stopped on graceful shutdown. If embedding this app,
retain that cleanup. Do not construct a pool at import time.

## Extend and distribute

Register additional tools in `app_module.py`. Keep destructive operations disabled
unless your application deliberately adds authorization and a reviewed policy.

```sh
nitrostack-py upgrade --version <released-version>
nitrostack-py validate
nitrostack-py pack
```

`upgrade` edits dependency requirements; run `nitrostack-py install` afterward.
The standalone QuickJS requirement remains in place across SDK upgrades.
`pack` includes the application modules and packaged JavaScript example.
Generation vendors the running SDK under `vendor/nitrostack`; uv resolves that
copy through `[tool.uv.sources]`.
