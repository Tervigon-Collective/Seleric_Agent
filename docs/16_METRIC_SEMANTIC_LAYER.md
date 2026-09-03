# 16 - Metric and Semantic Layer

## Why this precedes intelligence

If agents disagree about what `net_sales`, `CAC` or `ROAS` means, more reasoning makes the system less reliable, not more reliable.

## Each metric definition must specify

- canonical name,
- description,
- formula,
- numerator/denominator,
- source(s),
- event time/timezone,
- grain,
- dimensions,
- null/late-arriving policy,
- owner,
- version,
- validation tests.

## Metric IDs

Use immutable metric IDs and version definitions separately.

Example:

```yaml
id: metric.net_sales
version: 3
formula: gross_sales - returns - discounts_adjustment
owner: finance
```

## Agent rule

Agents ask for canonical metric IDs rather than improvising formulas in prompts whenever a registered metric exists.
