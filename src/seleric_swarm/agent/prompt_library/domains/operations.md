---
views: [returns, payments, stock_movements, stock_levels, stock_layers, stock_sync_errors, purchase_orders, purchase_order_lines, quality_checks, vendors, procurement_sampling]
---
**Operations** (returns, payments, stock, procurement)
- Returns are refund events on the refund date (cash clock) and ignore the chat's date axis; exchanges are not returns. For returned/RTO orders on the order's own timeline use commerce or finance.
- stock_levels is the current snapshot only; stock over time comes from stock_movements.
- Inventory and procurement views have their own clocks; there is no order-date axis on them.
