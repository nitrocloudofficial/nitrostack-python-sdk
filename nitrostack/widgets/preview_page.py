"""Live widget preview served by the MCP HTTP transport (Inspector cannot inject data)."""

from __future__ import annotations

PREVIEW_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Widget preview</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 16px; background: #f8fafc; color: #0f172a; }
    h1 { font-size: 1.2rem; margin: 0 0 8px; }
    .note { color: #475569; font-size: 0.9rem; max-width: 820px; line-height: 1.45; }
    .row { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin: 12px 0; }
    button, select { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid #cbd5e1; background: #fff; cursor: pointer; }
    button.primary { background: #ea580c; color: #fff; border-color: #c2410c; }
    iframe { width: 100%; height: 420px; border: 1px solid #e2e8f0; border-radius: 10px; background: #fff; transition: height .2s ease; }
    iframe.is-full { height: 90vh; }
    pre { background: #0f172a; color: #e2e8f0; padding: 12px; border-radius: 8px; overflow: auto; max-height: 180px; font-size: 12px; }
    textarea { width: 100%; height: 90px; font-family: ui-monospace, monospace; font-size: 12px; border: 1px solid #cbd5e1; border-radius: 8px; padding: 8px; }
    label { font-size: 0.85rem; color: #475569; }
  </style>
</head>
<body>
  <h1>Widget preview</h1>
  <p class="note">
    MCP Inspector Tools tab shows JSON. Open the <strong>Apps</strong> tab (or this
    page) to render the widget from the <em>live</em> <code>tools/call</code> result —
    not sample data. Use <code>NITROSTACK_APP_MODE=universal</code> for MCP Apps + OpenAI together (default is <code>openai</code>).
    For open shops only, keep <code>openNow: true</code> (or map <code>filter: open_now</code>).
    This page is <code>/widgets/preview</code> — not the static <code>widgets/preview.html</code> file.
  </p>
  <div class="row">
    <select id="tool"></select>
    <button class="primary" id="call">Call tool and render widget</button>
  </div>
  <label for="args">Tool arguments</label>
  <textarea id="args">{}</textarea>
  <iframe id="frame" title="Widget"></iframe>
  <h2 style="font-size:0.95rem;">structuredContent</h2>
  <pre id="json">Waiting for a tool call…</pre>
  <script>
    const tools = __TOOLS__;
    const select = document.getElementById("tool");
    const argsEl = document.getElementById("args");
    const frame = document.getElementById("frame");
    const jsonEl = document.getElementById("json");
    tools.forEach((t) => {
      const opt = document.createElement("option");
      opt.value = t.name;
      opt.textContent = t.name + " → " + t.resourceUri;
      select.appendChild(opt);
    });
    function selectedTool() {
      return tools.find((t) => t.name === select.value) || tools[0] || {};
    }
    function fillArgs() {
      argsEl.value = JSON.stringify(selectedTool().arguments || {}, null, 2);
    }
    function readArgs() {
      try { return JSON.parse(argsEl.value || "{}"); }
      catch (e) { throw new Error("Tool arguments must be valid JSON"); }
    }
    function inject(win, data, html) {
      if (html) frame.srcdoc = html;
      const send = () => {
        const target = frame.contentWindow;
        if (!target) return;
        const payload = { structuredContent: data };
        try {
          target.openai = Object.assign(target.openai || {}, { toolOutput: payload });
          target.dispatchEvent(new CustomEvent("openai:set_globals", {
            detail: { globals: { toolOutput: payload } }
          }));
        } catch (e) {}
        target.postMessage({ type: "setGlobals", globals: { toolOutput: payload } }, "*");
        target.postMessage({
          jsonrpc: "2.0",
          method: "ui/notifications/tool-result",
          params: payload
        }, "*");
      };
      frame.onload = () => setTimeout(send, 30);
      setTimeout(send, 80);
    }
    function reply(id, result, error) {
      const target = frame.contentWindow;
      if (!target || id == null) return;
      const msg = { jsonrpc: "2.0", id: id };
      if (error) msg.error = error;
      else msg.result = result == null ? {} : result;
      target.postMessage(msg, "*");
    }
    async function callAndRender(name, argumentsPayload) {
      jsonEl.textContent = "Calling " + name + "…";
      const resp = await fetch("/widgets/preview/call", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ tool: name, arguments: argumentsPayload || {} })
      });
      const body = await resp.json();
      if (!resp.ok || body.error) {
        jsonEl.textContent = JSON.stringify(body, null, 2);
        return body;
      }
      jsonEl.textContent = JSON.stringify(body.structuredContent, null, 2);
      const match = tools.find((t) => t.name === name);
      if (match) select.value = name;
      inject(frame.contentWindow, body.structuredContent, body.html);
      return body;
    }
    select.onchange = fillArgs;
    document.getElementById("call").onclick = async () => {
      let argumentsPayload = {};
      try { argumentsPayload = readArgs(); }
      catch (e) { jsonEl.textContent = String(e.message || e); return; }
      await callAndRender(select.value, argumentsPayload);
    };
    window.addEventListener("message", async (event) => {
      const msg = event.data;
      if (!msg || typeof msg !== "object" || msg.jsonrpc !== "2.0") return;
      if (msg.method === "ui/initialize") {
        reply(msg.id, {
          protocolVersion: "2026-01-26",
          hostCapabilities: { openLinks: {}, serverTools: {} },
          hostContext: { theme: "light", displayMode: "inline" }
        });
        return;
      }
      if (msg.method === "ui/notifications/initialized") return;
      if (msg.method === "tools/call") {
        const params = msg.params || {};
        try {
          const body = await callAndRender(params.name, params.arguments || {});
          if (body && !body.error) {
            reply(msg.id, { structuredContent: body.structuredContent, content: [] });
          } else {
            reply(msg.id, null, { message: (body && body.error) || "Tool call failed" });
          }
        } catch (e) {
          reply(msg.id, null, { message: String(e.message || e) });
        }
        return;
      }
      if (msg.method === "ui/open-link") {
        const url = (msg.params && msg.params.url) || "";
        if (/^(https?|mailto|tel):/i.test(String(url).trim())) {
          window.open(url, "_blank", "noopener");
        }
        reply(msg.id, {});
        return;
      }
      if (msg.method === "ui/request-display-mode") {
        const mode = (msg.params && msg.params.mode) || "inline";
        frame.classList.toggle("is-full", mode === "fullscreen" || mode === "pip");
        reply(msg.id, { mode: mode });
      }
    });
    if (tools.length) {
      const pizza = tools.find((t) => t.name.indexOf("pizza_list") >= 0) || tools[0];
      select.value = pizza.name;
      fillArgs();
    }
  </script>
</body>
</html>
"""


def render_preview_page(tools: list) -> str:
    from nitrostack.widgets.html_util import json_for_inline_script

    return PREVIEW_PAGE_HTML.replace("__TOOLS__", json_for_inline_script(tools))
