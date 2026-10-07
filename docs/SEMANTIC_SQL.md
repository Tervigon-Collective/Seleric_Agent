# Semantic SQL — Cube Core / Agentic System (Phase 6)

**Status:** 2026-10-06. Plan + code delivered (`PHASE6.md`); active build deferred from pre-aggregation build due to Cube 1.6.48 + ClickHouse index parsing limitation.

**What it is:** A read-only, governed SQL interface over the Cube semantic layer (`cube-v2`, port 4000 / 15432) for the `Seleric_Agent`. It complements (not replaces) `metrics_query`.

**When to use it:** When the analytical derivation requires CTEs, window functions, or cross-view compositions that `metrics_query`'s catalogue contract cannot express. Always prefer `metrics_query` for certified business numbers.

**When NOT to use it:** Not a replacement for raw ClickHouse SQL. The LLM must never write unvalidated SQL against `gold.*` or `serve.*` directly.

**Safety contract (hard rules, enforced in MCP wrapper + Cube):**
- Read-only SELECT / WITH / EXPLAIN only; no DDL/DML (`INSERT/UPDATE/DELETE/DROP/ALTER/CREATE/TRUNCATE/GRANT/REVOKE`); no destructive or blocking functions (`pg_sleep`, `pg_terminate_backend`, `pg_cancel_backend`, `set_config`, `copy`, `lo_import`).
- Must reference governed Cube view members or `MEASURE()`.
- Single statement; no `;` multi-statement injection.
- Max rows: 5000 default (hard cap 50,000); statement timeout: 30s; rate limit: 6/min per caller.
- Every call logs a `query_sha` (first 500 chars) and provenance (`catalogue_version`, `freshness`).

**Access / auth:**
- Postgres wire protocol: `postgresql://user:password@cube-v2:15432/cube` (`CUBEJS_DEV_MODE=true` allows any user/password; production DSN must replace `CUBE_SQL_DSN` env var).
- MCP tool: `semantic_sql` (registered in agent `TOOLS` list, gateway capability `seleric.semantic_sql`).
- ClickHouse access: remains restricted to `cube_serve` user (`SELECT serve.*` only); brand/module scoping enforced by the agent.

**Pre-aggregations / caching:**
- Cube Store (`cubestore:3030`) is running (`CUBEJS_CUBESTORE_URL=ws://cubestore:3030`).
- Pre-aggregation YAML defined for 3 hot grains (`pnl_daily` daily rollup by finance_channel; `orders` daily by sales_channel; `ad_delivery` daily by ad_platform/acct). Deferred from active build due to Cube 1.6.48 + ClickHouse driver index parsing limitation (`docs/semantic_v2/PHASE6.md`).

**Reference links:**
- Cube Core docs: `https://docs.cube.dev/docs/introduction` (semantic SQL, data modeling, APIs, caching)
- Cube SQL reference: `https://docs.cube.dev/reference/core-data-apis/sql-api` (query format, authentication, `MEASURE`)
- Cube pre-aggregation docs: `https://docs.cube.dev/docs/pre-aggregations/getting-started-pre-aggregations`
