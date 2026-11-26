// Calls are synchronous; no Node.js, fetch(), imports, or browser APIs are available.
const stock = ["keyboard", "monitor"].map(sku =>
    tools.call("inventory_check_stock", {sku})
);
const summary = tools.call("data_aggregate_sum", {values: stock.map(item => item.quantity)});
return {stock, total_units: summary.total};
