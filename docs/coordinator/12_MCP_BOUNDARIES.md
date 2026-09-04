# 12 — MCP Boundaries



Preferred path: **Coordinator → Domain Agent → DataProvider → MCPGateway → data.**



Coordinator must not hold unrestricted business MCP tools. Secrets never enter MissionState.



## Swarm providers (v1.11)



| `execution_mode` | Behavior |

| --- | --- |

| `fixture` (default) | Offline scenario providers only (`build_fixture_bundle`) |

| `staging` / `production` | `build_hybrid_bundle`: commerce + performance prefer MCP (`commerce.daily_sales`, `performance.daily_cac`); other domains stay on fixture; MCP miss → fixture fallback + limitations |



MCP fixture transports still stamp `data_origin=MCP` / `synthetic=true` (source marks fixture MCP). Live `seleric.*` streamable_http (when `SELERIC_MCP_URL` + token are set) can clear synthetic on overlay.



API: `POST /v1/missions` accepts `execution_mode`. Invalid values → 400.


