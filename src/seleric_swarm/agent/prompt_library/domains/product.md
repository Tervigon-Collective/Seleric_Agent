---
views: [product_profit, product_ads]
---
**Product** (product_profit = P&L before ads; product_ads = allocated ads)
- product_profit carries product/variant/SKU rows per order line, sliced by the order: the place for product rankings and mix.
- product_ads (spend, ROAS, profit after ads per product) is a model that spreads campaign-day spend over credited lines, order-date only, with an Unallocated row so totals equal delivery. Use it only when the question asks for ad-adjusted product results and say it is allocated.
