# Enterprise search

A small MCP service demonstrating BM25-ranked progressive tool discovery.
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

## Discover, then call

The initial tool list contains `search_tools`, `call_tool`, and three anchors:
`finance_get_financial_summary`, `inventory_report`, `support_get_system_status`.
The full six-tool catalog is available through progressive discovery.
BM25 ranks tool descriptions and schemas; it does not search an external
enterprise document database.

In an MCP client's tool-call interface:

1. Call `search_tools` with `{ "query": "" }` to browse the catalog.
2. Call `search_tools` with `{ "query": "overdue invoices" }`. The invoice tool
   appears first, with its input schema. A query with no matches returns no tools.
3. Call `call_tool` with:

```json
{"name": "finance_list_invoices", "arguments": {"overdue_only": true}}
```

The result contains `INV-001`, the overdue Acme invoice. Tool arguments use
schema fields directly; do not add an `input` wrapper.

## Extend and distribute

Add decorated read-only tools under `modules/`, register their classes in
`app_module.py`, and keep searchable names and descriptions specific.
`main.py` configures `Bm25SearchTransform` and the always-visible anchors.
Each read returns new containers so a client cannot mutate the sample records.

```sh
nitrostack-py upgrade --version <released-version>
nitrostack-py validate
nitrostack-py pack
```

`upgrade` edits dependency requirements; run `nitrostack-py install` afterward.
`pack` writes a wheel under `dist/`. Generation vendors the running SDK under
`vendor/nitrostack`; uv resolves that local copy through `[tool.uv.sources]`.
