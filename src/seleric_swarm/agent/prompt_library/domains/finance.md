---
views: [finance]
---
**Finance / P&L** (daily canonical P&L)
- "Net profit" ids mean contribution after ads: there is no operating-expense source, so never call it true profit.
- Ad spend here is by delivery date and is never allocated to orders; per-product or per-campaign profit after ads is the allocated model in the product domain.
- Use one date dimension per query. Product cost can be missing or provisional on some lines: it is shown as such, never as zero.
